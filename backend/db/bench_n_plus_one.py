"""Замер N+1: список мониторов с последней проверкой, «до» и «после» (неделя 4).

    uv run python db/bench_n_plus_one.py                    # N = 10, 50, 100
    uv run python db/bench_n_plus_one.py --sizes 20 100 --runs 30

«До»: страница мониторов, затем по запросу `list_recent(monitor_id, 1)` на каждый
монитор. «После»: `MonitorOverviewService.list_page`, один запрос за проверками на всю
страницу (`JOIN LATERAL ... LIMIT 1`).

Что считается честно:

* каждый прогон идёт на СВЕЖЕЙ сессии, иначе identity map спрятал бы повторные запросы;
* считаются только SELECT (событие `before_cursor_execute` на `sync_engine`); BEGIN и
  COMMIT драйвер отправляет мимо этого события;
* время это медиана по `--runs` прогонов после `--warmup` разогревающих: кеш PostgreSQL
  уже тёплый, и цифры сравнимы между собой, но не показывают «холодный» первый запрос;
* результаты обоих вариантов сравниваются: «быстро, но неверно» не бывает быстрым.

Читает от имени `pulse_app` (только SELECT). Смотрит как админ: у обычных пользователей
из сида по 8 мониторов, а для замера нужна страница побольше.
"""

import argparse
import asyncio
import os
import statistics
import sys
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from seed import DEFAULT_DSN, is_local
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine

from pulse.domain.monitors import MonitorWithLastCheck
from pulse.domain.users import Role, User
from pulse.repositories.checks import CheckRepository
from pulse.repositories.database import (
    Session,
    SessionFactory,
    create_engine,
    create_session_factory,
)
from pulse.repositories.monitors import MonitorRepository
from pulse.services.monitor_overview import MonitorOverviewService
from pulse.services.monitors import MonitorService

DEFAULT_SIZES = (10, 50, 100)
BENCH_ACTOR = User(id=0, email="bench@example.com", password_hash="", role=Role.ADMIN)

type Variant = Callable[[Session, int], Awaitable[list[MonitorWithLastCheck]]]


class SelectCounter:
    """Считает SELECT, ушедшие в базу через движок, пока подключён."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine.sync_engine
        self.count = 0
        event.listen(self._engine, "before_cursor_execute", self._on_execute)

    def _on_execute(
        self, conn: object, cursor: object, statement: str, *_: object
    ) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            self.count += 1

    def close(self) -> None:
        event.remove(self._engine, "before_cursor_execute", self._on_execute)


async def naive(session: Session, limit: int) -> list[MonitorWithLastCheck]:
    """1 + N: по запросу на последнюю проверку каждого монитора."""
    checks = CheckRepository(session)
    page = await MonitorService(MonitorRepository(session)).list_page(
        BENCH_ACTOR, limit=limit, offset=0
    )
    result: list[MonitorWithLastCheck] = []
    for monitor in page.items:
        recent = await checks.list_recent(monitor.id, 1)
        result.append(MonitorWithLastCheck.of(monitor, recent[0] if recent else None))
    return result


async def batched(session: Session, limit: int) -> list[MonitorWithLastCheck]:
    """Один запрос за проверками на всю страницу."""
    service = MonitorOverviewService(
        MonitorService(MonitorRepository(session)), CheckRepository(session)
    )
    return (await service.list_page(BENCH_ACTOR, limit=limit, offset=0)).items


@dataclass(frozen=True)
class Measurement:
    selects: int
    median_ms: float
    best_ms: float
    items: list[MonitorWithLastCheck]


async def measure(
    factory: SessionFactory,
    counter: SelectCounter,
    variant: Variant,
    limit: int,
    *,
    runs: int,
    warmup: int,
) -> Measurement:
    times: list[float] = []
    selects = 0
    items: list[MonitorWithLastCheck] = []
    for i in range(warmup + runs):
        async with factory() as session:  # свежая сессия: пустая identity map
            counter.count = 0
            started = time.perf_counter()
            items = await variant(session, limit)
            elapsed = (time.perf_counter() - started) * 1000
            selects = counter.count
        if i >= warmup:
            times.append(elapsed)
    return Measurement(selects, statistics.median(times), min(times), items)


def render(rows: Sequence[tuple[int, Measurement, Measurement]]) -> str:
    lines = [
        "| Мониторов | SELECT до | SELECT после | Время до, мс | Время после, мс "
        "| Быстрее в |",
        "| ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for size, before, after in rows:
        lines.append(
            f"| {size} | {before.selects} | {after.selects} | "
            f"{before.median_ms:.1f} | {after.median_ms:.1f} | "
            f"{before.median_ms / after.median_ms:.1f}× |"
        )
    return "\n".join(lines)


async def conditions(engine: AsyncEngine) -> str:
    async with engine.connect() as conn:
        version = await conn.scalar(text("SHOW server_version"))
        monitors = await conn.scalar(text("SELECT count(*) FROM monitors"))
        checks = await conn.scalar(text("SELECT count(*) FROM checks"))
    return f"PostgreSQL {version}; мониторов {monitors}, проверок {checks}"


async def run(dsn: str, sizes: Sequence[int], runs: int, warmup: int) -> int:
    engine = create_engine(dsn.replace("postgresql://", "postgresql+psycopg://", 1))
    factory = create_session_factory(engine)
    counter = SelectCounter(engine)
    try:
        print(await conditions(engine))
        rows: list[tuple[int, Measurement, Measurement]] = []
        for size in sizes:
            before = await measure(
                factory, counter, naive, size, runs=runs, warmup=warmup
            )
            after = await measure(
                factory, counter, batched, size, runs=runs, warmup=warmup
            )
            if before.items != after.items:
                print(
                    f"N={size}: результаты вариантов РАЗНЫЕ, замер не имеет смысла",
                    file=sys.stderr,
                )
                return 1
            rows.append((size, before, after))
        print(f"прогонов {runs} (+{warmup} разогрев), время: медиана\n")
        print(render(rows))
    finally:
        counter.close()
        await engine.dispose()
    return 0


def _positive(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("нужно число не меньше 1")
    return number


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Замер N+1: до и после")
    parser.add_argument("--sizes", type=_positive, nargs="+", default=DEFAULT_SIZES)
    parser.add_argument("--runs", type=_positive, default=15)
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument(
        "--dsn",
        default=os.environ.get("PULSE_SEED_DSN", DEFAULT_DSN),
        help="подключение pulse_app, иначе PULSE_SEED_DSN",
    )
    parser.add_argument("--allow-remote", action="store_true")
    args = parser.parse_args(argv)
    if not args.allow_remote and not is_local(args.dsn):
        print(
            "Сервер не локальный. Добавьте --allow-remote, если это нужно.",
            file=sys.stderr,
        )
        return 2
    return asyncio.run(run(args.dsn, args.sizes, args.runs, args.warmup))


if __name__ == "__main__":
    sys.exit(main())
