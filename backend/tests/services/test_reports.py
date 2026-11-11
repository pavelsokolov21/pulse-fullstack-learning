"""Отчёт монитора: cache-aside поверх Redis и PostgreSQL."""

import logging
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine

from pulse.domain.errors import MonitorNotFoundError
from pulse.domain.monitors import Monitor
from pulse.domain.reports import ReportWindow
from pulse.domain.users import Role, User
from pulse.repositories.audit_log import AuditLogRepository
from pulse.repositories.checks import CheckRepository
from pulse.repositories.database import Session
from pulse.repositories.monitors import MonitorRepository
from pulse.repositories.redis_client import RedisClient, create_redis
from pulse.repositories.report_cache import ReportCache
from pulse.services.checks import CheckService
from pulse.services.monitors import MonitorService
from pulse.services.reports import ReportService

NOW = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
ALICE = User(1, "alice@example.com", "h", Role.MANAGER)
BOB = User(2, "bob@example.com", "h", Role.MANAGER)
ADMIN = User(3, "admin@example.com", "h", Role.ADMIN)
WEEK = ReportWindow.WEEK
DAY = ReportWindow.DAY
UNKNOWN_ID = 2_000_000_000


@pytest.fixture(autouse=True)
async def _owners(persist_users: Callable[..., Awaitable[None]]) -> None:
    await persist_users(ALICE, BOB, ADMIN)


class Setup:
    def __init__(self, session: Session, redis: RedisClient) -> None:
        self.monitors = MonitorRepository(session)
        self.checks = CheckRepository(session)
        self.cache = ReportCache(redis, ttl_seconds=60)
        monitor_service = MonitorService(self.monitors, AuditLogRepository(session))
        self.reports = ReportService(
            monitor_service, self.checks, self.cache, clock=lambda: NOW
        )
        self.client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200))
        )
        self.check_service = CheckService(
            monitor_service, self.checks, self.client, self.cache, clock=lambda: NOW
        )

    async def monitor(
        self, owner: User = ALICE, url: str = "https://a.test"
    ) -> Monitor:
        return await self.monitors.create(
            owner_id=owner.id, url=url, interval=timedelta(minutes=5), timeout=5.0
        )

    async def add_check(
        self,
        monitor: Monitor,
        *,
        status: int | None = 200,
        latency: float = 10.0,
        ago: timedelta = timedelta(hours=1),
    ) -> None:
        await self.checks.add(
            monitor_id=monitor.id,
            url=monitor.url,
            status_code=status,
            latency_ms=latency,
            error=None if status is not None else "Network Error: ConnectError",
            checked_at=NOW - ago,
        )


@pytest.fixture
async def setup(session: Session, redis_client: RedisClient) -> Setup:
    return Setup(session, redis_client)


@pytest.fixture
def report_queries(engine: AsyncEngine) -> Iterator[list[str]]:
    """Запросы за отчётом, ушедшие в базу (узнаются по `percentile_cont`)."""
    seen: list[str] = []

    def record(conn: object, cursor: object, statement: str, *_: object) -> None:
        if "percentile_cont" in statement:
            seen.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    yield seen
    event.remove(engine.sync_engine, "before_cursor_execute", record)


async def test_first_read_computes_the_report_and_the_second_comes_from_cache(
    setup: Setup, report_queries: list[str]
) -> None:
    monitor = await setup.monitor()
    await setup.add_check(monitor)

    first = await setup.reports.get(ALICE, monitor.id, WEEK)
    second = await setup.reports.get(ALICE, monitor.id, WEEK)

    assert (first.cached, second.cached) == (False, True)
    assert first.report == second.report
    assert len(report_queries) == 1  # база за отчётом ходила один раз


async def test_report_contents(setup: Setup) -> None:
    monitor = await setup.monitor()
    await setup.add_check(monitor, status=200, latency=40.0)
    await setup.add_check(monitor, status=200, latency=60.0)
    await setup.add_check(monitor, status=503, latency=80.0)

    report = (await setup.reports.get(ALICE, monitor.id, WEEK)).report

    assert (report.total, report.ok) == (3, 2)
    assert report.uptime_percent == pytest.approx(66.667)
    assert report.avg_latency_ms == pytest.approx(60.0)
    assert report.generated_at == NOW
    assert (report.monitor_id, report.window) == (monitor.id, WEEK)


async def test_monitor_without_checks_has_empty_report(setup: Setup) -> None:
    monitor = await setup.monitor()

    report = (await setup.reports.get(ALICE, monitor.id, WEEK)).report

    assert (report.total, report.ok) == (0, 0)
    assert report.uptime_percent is None
    assert report.p95_latency_ms is None


async def test_report_right_after_a_new_check_is_not_stale(setup: Setup) -> None:
    # Главная проверка кеша: записали проверку и сразу прочитали отчёт.
    monitor = await setup.monitor()
    await setup.add_check(monitor)
    before = await setup.reports.get(ALICE, monitor.id, WEEK)
    assert before.report.total == 1
    assert (await setup.reports.get(ALICE, monitor.id, WEEK)).cached  # теперь в кеше

    await setup.check_service.run(ALICE, monitor.id)
    after = await setup.reports.get(ALICE, monitor.id, WEEK)

    assert after.cached is False
    assert after.report.total == 2


async def test_new_check_resets_every_window_of_that_monitor_only(setup: Setup) -> None:
    monitor = await setup.monitor()
    other = await setup.monitor(url="https://b.test")
    for m in (monitor, other):
        await setup.add_check(m)
        await setup.reports.get(ALICE, m.id, WEEK)
        await setup.reports.get(ALICE, m.id, DAY)

    await setup.check_service.run(ALICE, monitor.id)

    assert not (await setup.reports.get(ALICE, monitor.id, WEEK)).cached
    assert not (await setup.reports.get(ALICE, monitor.id, DAY)).cached
    assert (await setup.reports.get(ALICE, other.id, WEEK)).cached
    assert (await setup.reports.get(ALICE, other.id, DAY)).cached


async def test_windows_are_computed_separately(setup: Setup) -> None:
    monitor = await setup.monitor()
    await setup.add_check(monitor, ago=timedelta(hours=2))
    await setup.add_check(monitor, ago=timedelta(days=3))

    day = (await setup.reports.get(ALICE, monitor.id, DAY)).report
    week = (await setup.reports.get(ALICE, monitor.id, WEEK)).report

    assert (day.total, week.total) == (1, 2)


async def test_cached_report_of_a_foreign_monitor_is_not_served(setup: Setup) -> None:
    # Права проверяются раньше кеша: отчёт лежит в Redis, но Bob его не получит.
    monitor = await setup.monitor()
    await setup.add_check(monitor)
    await setup.reports.get(ALICE, monitor.id, WEEK)

    with pytest.raises(MonitorNotFoundError):
        await setup.reports.get(BOB, monitor.id, WEEK)


async def test_admin_reads_any_report(setup: Setup) -> None:
    monitor = await setup.monitor()
    await setup.add_check(monitor)

    assert (await setup.reports.get(ADMIN, monitor.id, WEEK)).report.total == 1


async def test_unknown_monitor_is_not_found_and_leaves_nothing_in_redis(
    setup: Setup, redis_client: RedisClient
) -> None:
    with pytest.raises(MonitorNotFoundError):
        await setup.reports.get(ALICE, UNKNOWN_ID, WEEK)

    assert await redis_client.dbsize() == 0  # pyright: ignore[reportUnknownMemberType]


async def test_report_is_still_computed_when_redis_is_down(
    session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    dead = create_redis("redis://:x@127.0.0.1:1/0")
    setup = Setup(session, dead)
    monitor = await setup.monitor()
    await setup.add_check(monitor)

    with caplog.at_level(logging.WARNING, logger="pulse.cache"):
        first = await setup.reports.get(ALICE, monitor.id, WEEK)
        second = await setup.reports.get(ALICE, monitor.id, WEEK)
    await dead.aclose()

    assert first.report.total == second.report.total == 1
    assert (first.cached, second.cached) == (False, False)  # кеша нет, ответ есть
