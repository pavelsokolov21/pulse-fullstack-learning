"""Фоновый «нагрузчик» для практики недели 7: без него блокировки не видно.

Цикл из N воркеров: вставляет проверку и читает последние по монитору, как это делает
приложение. Каждый запрос замеряется; раз в секунду печатается строка с числом операций,
медианой и максимумом задержки. Блокировка миграции видна как секунда с огромным max
(запрос стоит в очереди за ALTER TABLE) или как ошибки.

Запуск (адрес базы в окружении, пароль в репозиторий не кладём):

    export PULSE_LAB_URL='postgresql://pulse_app:ПАРОЛЬ@127.0.0.1:5432/pulse_lab'
    uv run python db/lab/loader.py --rate 200 --seconds 60

`--column` выбирает, как «ведёт себя код»: пишет и читает старую колонку
(latency_ms) или новую (duration_ms). Так проверяется, что между миграциями
expand / migrate / contract работают обе версии приложения.
"""

import argparse
import asyncio
import contextlib
import os
import random
import signal
import statistics
import time
from dataclasses import dataclass, field

import psycopg
from psycopg import sql

STALL_MS = 100.0  # дольше считаем запрос «застрявшим»
RECENT = 20


@dataclass
class Sample:
    at: float
    op: str
    ms: float
    ok: bool


@dataclass
class Stats:
    samples: list[Sample] = field(default_factory=lambda: [])
    errors: dict[str, int] = field(default_factory=lambda: {})

    def add(self, op: str, started: float, ok: bool) -> None:
        now = time.perf_counter()
        self.samples.append(Sample(now, op, (now - started) * 1000, ok))


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * q))]


async def worker(
    url: str,
    monitor_ids: list[int],
    column: str,
    interval: float,
    stop: asyncio.Event,
    stats: Stats,
) -> None:
    insert = sql.SQL(
        "INSERT INTO checks (monitor_id, url, status_code, {col}, checked_at)"
        " VALUES (%s, 'https://lab.example/', 200, %s, now())"
    ).format(col=sql.Identifier(column))
    select = sql.SQL(
        "SELECT id, status_code, {col}, checked_at FROM checks"
        " WHERE monitor_id = %s ORDER BY checked_at DESC LIMIT %s"
    ).format(col=sql.Identifier(column))
    conn: psycopg.AsyncConnection[tuple[object, ...]] | None = None
    while not stop.is_set():
        started_cycle = time.perf_counter()
        monitor_id = random.choice(monitor_ids)
        for op, query, params in (
            ("insert", insert, (monitor_id, random.uniform(20, 400))),
            ("select", select, (monitor_id, RECENT)),
        ):
            started = time.perf_counter()
            try:
                if conn is None or conn.closed:
                    conn = await psycopg.AsyncConnection.connect(url, autocommit=True)
                cur = await conn.execute(query, params)
                if op == "select":
                    await cur.fetchall()
                stats.add(op, started, True)
            except psycopg.Error as exc:
                stats.add(op, started, False)
                name = type(exc).__name__
                stats.errors[name] = stats.errors.get(name, 0) + 1
                if conn is not None:
                    await conn.close()
                conn = None
        pause = interval - (time.perf_counter() - started_cycle)
        if pause > 0:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), pause)
    if conn is not None:
        await conn.close()


async def reporter(stats: Stats, stop: asyncio.Event) -> None:
    print("сек   опер  p50,мс   max,мс  ошибок", flush=True)
    second = 0
    seen = 0
    while not stop.is_set():
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), 1.0)
        second += 1
        chunk = stats.samples[seen:]
        seen = len(stats.samples)
        if not chunk:
            print(
                f"{second:>3}      0       -        -       -  ← ничего не завершилось",
                flush=True,
            )
            continue
        ms = [s.ms for s in chunk]
        bad = sum(1 for s in chunk if not s.ok)
        flag = "  ← ЗАСТРЯЛО" if max(ms) > STALL_MS else ""
        print(
            f"{second:>3} {len(chunk):>6} {statistics.median(ms):>7.1f} {max(ms):>8.1f}"
            f" {bad:>7}{flag}",
            flush=True,
        )


def summary(stats: Stats, started: float) -> None:
    print("\n=== итог ===")
    for op in ("insert", "select"):
        ms = [s.ms for s in stats.samples if s.op == op and s.ok]
        if ms:
            print(
                f"{op:<7} n={len(ms):>6}  p50={percentile(ms, 0.5):7.1f}"
                f"  p99={percentile(ms, 0.99):8.1f}  max={max(ms):9.1f} мс"
            )
    stalls = [s for s in stats.samples if s.ms > STALL_MS]
    print(f"застрявших (> {STALL_MS:.0f} мс): {len(stalls)}")
    print(f"ошибки: {stats.errors or 'нет'}")
    print(f"длительность: {time.perf_counter() - started:.1f} с")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Нагрузчик для практики миграций")
    parser.add_argument("--rate", type=float, default=200, help="циклов в секунду")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--seconds", type=float, default=0, help="0 = до Ctrl+C")
    parser.add_argument(
        "--column", default="latency_ms", choices=["latency_ms", "duration_ms"]
    )
    args = parser.parse_args()

    url = os.environ.get("PULSE_LAB_URL")
    if not url:
        raise SystemExit(
            "Задайте PULSE_LAB_URL (postgresql://pulse_app:ПАРОЛЬ@127.0.0.1:5432/pulse_lab)"
        )
    if "pulse_lab" not in url:
        raise SystemExit(
            "Нагрузчик пишет в базу: разрешён только pulse_lab, рабочую не трогаем"
        )

    async with await psycopg.AsyncConnection.connect(url, autocommit=True) as probe:
        cur = await probe.execute("SELECT id FROM monitors ORDER BY id")
        monitor_ids = [int(row[0]) for row in await cur.fetchall()]

    stats = Stats()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    started = time.perf_counter()
    interval = args.workers / args.rate
    tasks = [
        asyncio.create_task(
            worker(url, monitor_ids, args.column, interval, stop, stats)
        )
        for _ in range(args.workers)
    ]
    tasks.append(asyncio.create_task(reporter(stats, stop)))
    if args.seconds:
        loop.call_later(args.seconds, stop.set)
    await asyncio.gather(*tasks)
    summary(stats, started)


if __name__ == "__main__":
    asyncio.run(main())
