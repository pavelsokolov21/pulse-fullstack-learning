"""Замер кеша отчёта: промах и попадание (неделя 6).

    uv run python db/bench_report_cache.py                    # окно 30d, самый
                                                              # «тяжёлый» монитор
    uv run python db/bench_report_cache.py --window 7d --runs 50 --monitor-id 30

Адрес Redis берётся из `--redis-url` или `PULSE_API_REDIS_URL` (в нём пароль из
`.env`, в репозитории его нет), адрес PostgreSQL из `--dsn` (роль `pulse_app`).

«Промах»: перед каждым прогоном кеш сбрасывается (`invalidate`), отчёт считается по
базе и кладётся в Redis. «Попадание»: отчёт уже лежит в Redis. Каждый прогон идёт на
СВЕЖЕЙ сессии (пустая identity map), считаются только SELECT (событие
`before_cursor_execute`), время это медиана после разогрева.

Из SELECT один всегда проверка прав (`MonitorService.get`: чужой отчёт нельзя отдать
из кеша), он есть и при попадании. Дорогой запрос за статистикой (`percentile_cont`)
при попадании исчезает.
"""

import argparse
import asyncio
import os
import statistics
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass

from bench_n_plus_one import BENCH_ACTOR, SelectCounter
from seed import DEFAULT_DSN, is_local
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine

from pulse.domain.reports import ReportWindow
from pulse.repositories.audit_log import AuditLogRepository
from pulse.repositories.checks import CheckRepository
from pulse.repositories.database import (
    SessionFactory,
    create_engine,
    create_session_factory,
)
from pulse.repositories.monitors import MonitorRepository
from pulse.repositories.redis_client import create_redis, ping
from pulse.repositories.report_cache import ReportCache
from pulse.services.monitors import MonitorService
from pulse.services.reports import ReportService


class StatsCounter:
    """Считает запросы именно за статистикой отчёта (узнаются по `percentile_cont`)."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine.sync_engine
        self.count = 0
        event.listen(self._engine, "before_cursor_execute", self._on_execute)

    def _on_execute(
        self, conn: object, cursor: object, statement: str, *_: object
    ) -> None:
        if "percentile_cont" in statement:
            self.count += 1

    def close(self) -> None:
        event.remove(self._engine, "before_cursor_execute", self._on_execute)


@dataclass(frozen=True)
class Measurement:
    selects: int
    report_queries: int
    median_ms: float
    best_ms: float
    cached: bool
    total_checks: int


async def heaviest_monitor(engine: AsyncEngine) -> int:
    async with engine.connect() as conn:
        monitor_id = await conn.scalar(
            text(
                "SELECT monitor_id FROM checks GROUP BY monitor_id "
                "ORDER BY count(*) DESC LIMIT 1"
            )
        )
    if monitor_id is None:
        raise SystemExit("В таблице checks нет данных: сначала db/seed_bulk.py")
    return int(monitor_id)


async def measure(
    factory: SessionFactory,
    cache: ReportCache,
    selects: SelectCounter,
    stats: StatsCounter,
    monitor_id: int,
    window: ReportWindow,
    *,
    hit: bool,
    runs: int,
    warmup: int,
) -> Measurement:
    times: list[float] = []
    last_selects = last_stats = total = 0
    cached = False
    for i in range(warmup + runs):
        if not hit:
            await cache.invalidate(monitor_id)  # каждый прогон начинается с промаха
        async with factory() as session:  # свежая сессия: пустая identity map
            service = ReportService(
                MonitorService(MonitorRepository(session), AuditLogRepository(session)),
                CheckRepository(session),
                cache,
            )
            selects.count = stats.count = 0
            started = time.perf_counter()
            result = await service.get(BENCH_ACTOR, monitor_id, window)
            elapsed = (time.perf_counter() - started) * 1000
            last_selects, last_stats = selects.count, stats.count
            cached, total = result.cached, result.report.total
        if i >= warmup:
            times.append(elapsed)
    return Measurement(
        last_selects, last_stats, statistics.median(times), min(times), cached, total
    )


def render(window: str, miss: Measurement, hit: Measurement) -> str:
    lines = [
        f"Окно {window}, проверок в окне: {miss.total_checks}",
        "",
        "| Случай | SELECT всего | из них за отчётом | Время, мс (медиана) "
        "| Лучшее, мс |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for name, m in (("Промах", miss), ("Попадание", hit)):
        lines.append(
            f"| {name} | {m.selects} | {m.report_queries} | "
            f"{m.median_ms:.2f} | {m.best_ms:.2f} |"
        )
    lines.append("")
    lines.append(f"Ускорение по медиане: {miss.median_ms / hit.median_ms:.1f}×")
    return "\n".join(lines)


async def run(
    dsn: str,
    redis_url: str,
    monitor: int | None,
    window: ReportWindow,
    runs: int,
    warmup: int,
) -> int:
    engine = create_engine(dsn.replace("postgresql://", "postgresql+psycopg://", 1))
    redis = create_redis(redis_url)
    selects = SelectCounter(engine)
    stats = StatsCounter(engine)
    try:
        await ping(redis)
        monitor_id = monitor if monitor is not None else await heaviest_monitor(engine)
        factory = create_session_factory(engine)
        cache = ReportCache(redis, ttl_seconds=600)
        miss = await measure(
            factory,
            cache,
            selects,
            stats,
            monitor_id,
            window,
            hit=False,
            runs=runs,
            warmup=warmup,
        )
        hit = await measure(
            factory,
            cache,
            selects,
            stats,
            monitor_id,
            window,
            hit=True,
            runs=runs,
            warmup=warmup,
        )
        if miss.cached or not hit.cached:
            print(
                "Сценарии перепутаны: промах попал в кеш или попадание нет",
                file=sys.stderr,
            )
            return 1
        print(f"Монитор {monitor_id}")
        print(render(window.value, miss, hit))
    finally:
        selects.close()
        stats.close()
        await redis.aclose()
        await engine.dispose()
    return 0


def _positive(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("нужно число не меньше 1")
    return number


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Замер кеша отчёта: промах и попадание"
    )
    parser.add_argument(
        "--window",
        type=ReportWindow,
        choices=list(ReportWindow),
        default=ReportWindow.MONTH,
    )
    parser.add_argument(
        "--monitor-id",
        type=int,
        default=None,
        help="по умолчанию монитор с наибольшим числом проверок",
    )
    parser.add_argument("--runs", type=_positive, default=30)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument(
        "--dsn",
        default=os.environ.get("PULSE_SEED_DSN", DEFAULT_DSN),
        help="PostgreSQL, роль pulse_app",
    )
    parser.add_argument(
        "--redis-url",
        default=os.environ.get("PULSE_API_REDIS_URL"),
        help="Redis, иначе PULSE_API_REDIS_URL",
    )
    parser.add_argument("--allow-remote", action="store_true")
    args = parser.parse_args(argv)
    if not args.redis_url:
        print(
            "Укажите адрес Redis: --redis-url или PULSE_API_REDIS_URL", file=sys.stderr
        )
        return 2
    if not args.allow_remote and not is_local(args.dsn):
        print(
            "Сервер не локальный. Добавьте --allow-remote, если это нужно.",
            file=sys.stderr,
        )
        return 2
    return asyncio.run(
        run(
            args.dsn,
            args.redis_url,
            args.monitor_id,
            args.window,
            args.runs,
            args.warmup,
        )
    )


if __name__ == "__main__":
    sys.exit(main())
