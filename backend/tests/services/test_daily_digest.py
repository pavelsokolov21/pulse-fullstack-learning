from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest

import pulse.services.daily_digest as digest_module
from pulse.domain.users import Role, User
from pulse.repositories.checks import CheckRepository
from pulse.repositories.database import Session
from pulse.repositories.monitors import MonitorRepository
from pulse.services.daily_digest import DailyDigestService

NOW = datetime(2026, 1, 10, 3, 0, tzinfo=UTC)
OWNER = User(1, "owner@example.com", "h", Role.MANAGER)


class Env:
    def __init__(self, session: Session) -> None:
        self.monitors = MonitorRepository(session)
        self.checks = CheckRepository(session)
        self.service = DailyDigestService(self.monitors, self.checks, clock=lambda: NOW)

    async def monitor(self, url: str) -> int:
        created = await self.monitors.create(
            owner_id=OWNER.id, url=url, interval=timedelta(minutes=5), timeout=10
        )
        return created.id

    async def check(self, monitor_id: int, status: int, hours_ago: float = 1) -> None:
        await self.checks.add(
            monitor_id=monitor_id,
            url="https://x.test",
            status_code=status,
            latency_ms=5.0,
            error=None,
            checked_at=NOW - timedelta(hours=hours_ago),
        )


@pytest.fixture
async def env(session: Session, persist_users: Callable[..., Awaitable[None]]) -> Env:
    await persist_users(OWNER)
    return Env(session)


async def test_digest_totals_cover_all_monitors(env: Env) -> None:
    a = await env.monitor("https://a.test")
    b = await env.monitor("https://b.test")
    for status in (200, 200, 503):
        await env.check(a, status)
    await env.check(b, 200)

    digest = await env.service.build()

    assert (digest.monitors, digest.total, digest.ok) == (2, 4, 3)
    assert digest.uptime_percent == 75.0


async def test_period_is_the_last_24_hours(env: Env) -> None:
    a = await env.monitor("https://a.test")
    await env.check(a, 503, hours_ago=25)  # не входит
    await env.check(a, 200, hours_ago=23)

    digest = await env.service.build()

    assert (digest.total, digest.ok) == (1, 1)
    assert digest.since == NOW - timedelta(hours=24)


async def test_monitor_without_checks_is_counted_but_not_listed(env: Env) -> None:
    await env.monitor("https://quiet.test")

    digest = await env.service.build()

    assert (digest.monitors, digest.total, digest.worst) == (1, 0, [])
    assert digest.uptime_percent is None


async def test_worst_monitors_come_first(env: Env) -> None:
    good = await env.monitor("https://good.test")
    bad = await env.monitor("https://bad.test")
    await env.check(good, 200)
    await env.check(bad, 503)
    await env.check(bad, 200)

    digest = await env.service.build()

    assert [(line.url, line.uptime_percent) for line in digest.worst] == [
        ("https://bad.test", 50.0),
        ("https://good.test", 100.0),
    ]


async def test_only_the_worst_few_are_kept(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(digest_module, "WORST_COUNT", 2)
    for n in range(4):
        monitor_id = await env.monitor(f"https://m{n}.test")
        await env.check(monitor_id, 200)

    digest = await env.service.build()

    assert len(digest.worst) == 2


async def test_all_pages_of_monitors_are_read(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(digest_module, "PAGE_SIZE", 2)
    for n in range(5):
        monitor_id = await env.monitor(f"https://m{n}.test")
        await env.check(monitor_id, 200)

    digest = await env.service.build()

    assert (digest.monitors, digest.total) == (5, 5)
