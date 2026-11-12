"""Очередь блокировок при ALTER TABLE: как одна долгая транзакция останавливает таблицу.

Сцена из четырёх соединений к pulse_lab:
  A  долгая транзакция: прочитала checks и не закрылась (отчёт, забытый BEGIN);
  B  миграция: ALTER TABLE checks ADD COLUMN (нужен ACCESS EXCLUSIVE);
  C  обычный SELECT приложения, пришедший уже после B;
  D  наблюдатель: смотрит pg_stat_activity и pg_locks.

Раунд 1: без lock_timeout. Раунд 2: с lock_timeout = 1 с. Запуск:

    export PULSE_MIGRATE_DATABASE_URL='postgresql+psycopg://pulse_migrator:ПАРОЛЬ@127.0.0.1:5432/pulse_lab'
    uv run python db/lab/lock_demo.py
"""

import asyncio
import os
import time
from collections.abc import Awaitable
from typing import cast

import psycopg

Conn = psycopg.AsyncConnection[tuple[object, ...]]

HOLD_SECONDS = 3.0  # сколько A держит транзакцию
C_STARTS_AFTER = 0.5  # через сколько после B приходит читатель C
SNAPSHOT_AFTER = 0.9  # когда D снимает картину (после старта B)


async def connect(url: str, *, autocommit: bool) -> Conn:
    return await psycopg.AsyncConnection.connect(url, autocommit=autocommit)


async def backend_pid(conn: Conn) -> int:
    cur = await conn.execute("SELECT pg_backend_pid()")
    row = await cur.fetchone()
    assert row is not None
    return int(str(row[0]))


def table(headers: list[str], rows: list[list[str]]) -> None:
    widths = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(headers)]
    print("  " + "  ".join(h.ljust(w) for h, w in zip(headers, widths, strict=True)))
    for row in rows:
        print("  " + "  ".join(c.ljust(w) for c, w in zip(row, widths, strict=True)))


async def snapshot(observer: Conn, names: dict[int, str]) -> None:
    def who(pid: object) -> str:
        return names.get(int(str(pid)), f"pid {pid}")

    cur = await observer.execute(
        "SELECT pid, state, wait_event_type, wait_event, pg_blocking_pids(pid),"
        " left(query, 46) FROM pg_stat_activity"
        " WHERE datname = current_database() AND pid <> pg_backend_pid()"
        " AND pid = ANY(%s) ORDER BY backend_start",
        (list(names),),
    )
    print("  pg_stat_activity:")
    table(
        ["кто", "state", "wait", "ждёт сессию", "запрос"],
        [
            [
                who(pid),
                str(state),
                f"{etype or '-'}/{event or '-'}",
                ",".join(who(b) for b in cast("list[int]", blocked)) or "-",
                str(query).replace("\n", " "),
            ]
            for pid, state, etype, event, blocked, query in await cur.fetchall()
        ],
    )
    cur = await observer.execute(
        "SELECT pid, mode, granted FROM pg_locks"
        " WHERE relation = 'checks'::regclass AND pid = ANY(%s)"
        " ORDER BY granted DESC, pid",
        (list(names),),
    )
    print("  pg_locks по таблице checks:")
    table(
        ["кто", "режим", "выдана"],
        [
            [who(p), str(m), "да" if g else "НЕТ, ждёт"]
            for p, m, g in await cur.fetchall()
        ],
    )


async def timed(coro: Awaitable[None]) -> tuple[float, str]:
    started = time.perf_counter()
    try:
        await coro
        outcome = "выполнено"
    except psycopg.errors.LockNotAvailable:
        outcome = "ОШИБКА lock_timeout: «canceling statement due to lock timeout»"
    return time.perf_counter() - started, outcome


async def round_(url: str, title: str, lock_timeout: str | None) -> None:
    print(f"\n=== {title}")
    a = await connect(url, autocommit=False)
    b = await connect(url, autocommit=True)
    c = await connect(url, autocommit=True)
    d = await connect(url, autocommit=True)
    names = {
        await backend_pid(a): "A (долгое чтение)",
        await backend_pid(b): "B (миграция)",
        await backend_pid(c): "C (SELECT приложения)",
    }
    try:
        await a.execute("SELECT count(*) FROM checks WHERE monitor_id = 1")
        print(
            "A: открыл транзакцию, прочитал checks (держит ACCESS SHARE) и не закрывает"
        )

        async def migrate() -> None:
            if lock_timeout:
                await b.execute(
                    "SELECT set_config('lock_timeout', %s, false)", (lock_timeout,)
                )
            await b.execute("ALTER TABLE checks ADD COLUMN lock_probe integer")

        async def reader() -> None:
            await asyncio.sleep(C_STARTS_AFTER)
            await c.execute("SELECT id FROM checks LIMIT 1")

        migrate_task = asyncio.create_task(timed(migrate()))
        reader_task = asyncio.create_task(timed(reader()))
        await asyncio.sleep(SNAPSHOT_AFTER)
        await snapshot(d, names)
        await asyncio.sleep(max(0.0, HOLD_SECONDS - SNAPSHOT_AFTER))
        await a.rollback()
        print(f"A: закрыл транзакцию через {HOLD_SECONDS:.1f} с")
        b_time, b_outcome = await migrate_task
        c_time, _ = await reader_task
        c_time -= C_STARTS_AFTER
        print(f"B (ALTER TABLE): {b_outcome}, ждал {b_time:.2f} с")
        print(f"C (обычный SELECT): ждал {c_time:.2f} с, хотя сам никому не мешал")
    finally:
        await d.execute("ALTER TABLE checks DROP COLUMN IF EXISTS lock_probe")
        for conn in (a, b, c, d):
            await conn.close()


async def main() -> None:
    raw = os.environ.get("PULSE_MIGRATE_DATABASE_URL", "")
    url = raw.replace("postgresql+psycopg://", "postgresql://", 1)
    if "pulse_lab" not in url:
        raise SystemExit("Нужен PULSE_MIGRATE_DATABASE_URL на базу pulse_lab")
    await round_(url, "Раунд 1: ALTER TABLE без lock_timeout", None)
    await round_(url, "Раунд 2: тот же ALTER TABLE с lock_timeout = 1s", "1s")


if __name__ == "__main__":
    asyncio.run(main())
