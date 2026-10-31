from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from pulse.checks.models import CheckResult
from pulse.domain.checks import Check
from pulse.domain.errors import MonitorNotFoundError
from pulse.domain.monitors import Monitor
from pulse.domain.users import Role, User
from pulse.repositories.checks import CheckRepository
from pulse.repositories.monitors import MonitorRepository
from pulse.services.checks import CheckService
from pulse.services.monitors import MonitorService

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
ALICE = User(1, "alice@example.com", "h", Role.MANAGER)
BOB = User(2, "bob@example.com", "h", Role.MANAGER)
ADMIN = User(3, "admin@example.com", "h", Role.ADMIN)

Handler = Callable[[httpx.Request], httpx.Response]


class Env:
    """Сервис, собранный на MockTransport, с подставными часами."""

    def __init__(self, handler: Handler) -> None:
        self.requests: list[httpx.Request] = []

        def recording(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return handler(request)

        self.monitors = MonitorRepository()
        self.checks = CheckRepository()
        self.monitor_service = MonitorService(self.monitors, self.checks)
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(recording))
        self.now = NOW
        self.service = CheckService(
            self.monitor_service, self.checks, self.client, clock=lambda: self.now
        )

    def add_monitor(
        self, url: str = "https://a.test", timeout: float = 10.0
    ) -> Monitor:
        return self.monitors.create(
            owner_id=ALICE.id, url=url, interval=timedelta(minutes=5), timeout=timeout
        )


def ok_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200)


@pytest.fixture
async def env() -> AsyncIterator[Env]:
    environment = Env(ok_handler)
    yield environment
    await environment.client.aclose()


async def make_env(handler: Handler) -> Env:
    return Env(handler)


# --- run -------------------------------------------------------------------


async def test_run_stores_and_returns_the_check(env: Env) -> None:
    monitor = env.add_monitor()

    check = await env.service.run(ALICE, monitor.id)

    assert check.monitor_id == monitor.id
    assert check.url == "https://a.test"
    assert (check.status_code, check.error, check.ok) == (200, None, True)
    assert check.checked_at == NOW
    assert check.latency_ms >= 0
    assert env.checks.list_recent(monitor.id, 10) == [check]


async def test_run_requests_the_monitor_url_once(env: Env) -> None:
    monitor = env.add_monitor("https://a.test/health")

    await env.service.run(ALICE, monitor.id)

    assert [str(r.url) for r in env.requests] == ["https://a.test/health"]


async def test_run_uses_the_clock_for_checked_at(env: Env) -> None:
    monitor = env.add_monitor()
    first = await env.service.run(ALICE, monitor.id)
    env.now = NOW + timedelta(minutes=5)
    second = await env.service.run(ALICE, monitor.id)

    assert (first.checked_at, second.checked_at) == (NOW, NOW + timedelta(minutes=5))


async def test_run_checked_at_is_timezone_aware_by_default() -> None:
    environment = Env(ok_handler)
    service = CheckService(
        environment.monitor_service, environment.checks, environment.client
    )
    monitor = environment.add_monitor()

    check = await service.run(ALICE, monitor.id)
    await environment.client.aclose()

    assert check.checked_at.tzinfo is not None
    assert check.checked_at.utcoffset() == timedelta(0)


@pytest.mark.parametrize(
    ("status", "ok"),
    [(200, True), (204, True), (301, True), (404, False), (500, False), (503, False)],
)
async def test_run_ok_follows_the_status_code(status: int, ok: bool) -> None:
    environment = await make_env(lambda request: httpx.Response(status))
    monitor = environment.add_monitor()

    check = await environment.service.run(ALICE, monitor.id)
    await environment.client.aclose()

    assert (check.status_code, check.ok) == (status, ok)


async def test_run_records_network_failure_as_failed_check() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    environment = await make_env(refuse)
    monitor = environment.add_monitor()

    check = await environment.service.run(ALICE, monitor.id)
    await environment.client.aclose()

    assert check.status_code is None
    assert check.ok is False
    assert check.error == "Network Error: ConnectError"
    assert environment.checks.list_recent(monitor.id, 10) == [check]


async def test_run_passes_monitor_timeout_to_the_request(env: Env) -> None:
    monitor = env.add_monitor(timeout=2.5)

    await env.service.run(ALICE, monitor.id)

    assert env.requests[0].extensions["timeout"]["read"] == 2.5


async def test_run_unknown_monitor_raises_and_makes_no_request(env: Env) -> None:
    with pytest.raises(MonitorNotFoundError):
        await env.service.run(ALICE, 99)

    assert env.requests == []


async def test_check_keeps_the_url_that_was_checked(env: Env) -> None:
    monitor = env.add_monitor("https://old.test")
    old = await env.service.run(ALICE, monitor.id)
    env.monitors.update(monitor.id, url="https://new.test")
    new = await env.service.run(ALICE, monitor.id)

    assert (old.url, new.url) == ("https://old.test", "https://new.test")
    assert env.checks.list_recent(monitor.id, 10)[1].url == "https://old.test"


async def test_monitor_deleted_during_request_stores_nothing() -> None:
    environment: Env

    def delete_while_in_flight(request: httpx.Request) -> httpx.Response:
        environment.monitors.delete(1)
        return httpx.Response(200)

    environment = Env(delete_while_in_flight)
    environment.add_monitor()

    with pytest.raises(MonitorNotFoundError):
        await environment.service.run(ALICE, 1)
    await environment.client.aclose()

    assert environment.checks.list_recent(1, 10) == []


async def test_invalid_url_error_becomes_a_failed_check(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(
        client: httpx.AsyncClient, url: str, **kwargs: object
    ) -> CheckResult:
        raise ValueError("bad url")

    monkeypatch.setattr("pulse.services.checks.check_url", broken)
    monitor = env.add_monitor()

    check = await env.service.run(ALICE, monitor.id)

    assert check.status_code is None
    assert check.ok is False
    assert check.error == "Invalid URL: bad url"
    assert check.latency_ms == 0.0


# --- list_recent -----------------------------------------------------------


async def test_list_recent_returns_newest_first_with_limit(env: Env) -> None:
    monitor = env.add_monitor()
    checks: list[Check] = [await env.service.run(ALICE, monitor.id) for _ in range(4)]

    assert env.service.list_recent(ALICE, monitor.id, 2) == [checks[3], checks[2]]


async def test_list_recent_of_new_monitor_is_empty(env: Env) -> None:
    monitor = env.add_monitor()

    assert env.service.list_recent(ALICE, monitor.id, 10) == []


async def test_list_recent_unknown_monitor_raises(env: Env) -> None:
    with pytest.raises(MonitorNotFoundError):
        env.service.list_recent(ALICE, 99, 10)


async def test_histories_of_different_monitors_are_separate(env: Env) -> None:
    first = env.add_monitor("https://a.test")
    second = env.add_monitor("https://b.test")
    check = await env.service.run(ALICE, first.id)

    assert env.service.list_recent(ALICE, first.id, 10) == [check]
    assert env.service.list_recent(ALICE, second.id, 10) == []


# --- владение --------------------------------------------------------------


async def test_run_on_foreign_monitor_is_not_found_and_makes_no_request(
    env: Env,
) -> None:
    monitor = env.add_monitor()  # владелец Alice

    with pytest.raises(MonitorNotFoundError):
        await env.service.run(BOB, monitor.id)

    assert env.requests == []
    assert env.checks.list_recent(monitor.id, 10) == []


async def test_list_recent_of_foreign_monitor_is_not_found(env: Env) -> None:
    monitor = env.add_monitor()
    await env.service.run(ALICE, monitor.id)

    with pytest.raises(MonitorNotFoundError):
        env.service.list_recent(BOB, monitor.id, 10)


async def test_admin_can_run_and_read_checks_of_any_monitor(env: Env) -> None:
    monitor = env.add_monitor()

    check = await env.service.run(ADMIN, monitor.id)

    assert check.monitor_id == monitor.id
    assert env.service.list_recent(ADMIN, monitor.id, 10) == [check]
