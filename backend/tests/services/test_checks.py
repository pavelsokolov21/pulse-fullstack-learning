import inspect
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from pulse.checks.models import CheckResult
from pulse.domain.checks import Check
from pulse.domain.errors import MonitorNotFoundError
from pulse.domain.monitors import Monitor
from pulse.domain.users import Role, User
from pulse.repositories.checks import CheckRepository
from pulse.repositories.database import Session
from pulse.repositories.monitors import MonitorRepository
from pulse.services.checks import CheckService
from pulse.services.monitors import MonitorService

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
ALICE = User(1, "alice@example.com", "h", Role.MANAGER)
BOB = User(2, "bob@example.com", "h", Role.MANAGER)
ADMIN = User(3, "admin@example.com", "h", Role.ADMIN)

UNKNOWN_ID = 2_000_000_000
Handler = Callable[[httpx.Request], httpx.Response | Awaitable[httpx.Response]]


class Env:
    """Сервис, собранный на MockTransport, с подставными часами."""

    def __init__(self, handler: Handler, session: Session) -> None:
        self.requests: list[httpx.Request] = []
        self.session = session
        # Была ли у сессии открытая транзакция, когда ушёл HTTP-запрос.
        self.transaction_open_during_request: list[bool] = []

        async def recording(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            self.transaction_open_during_request.append(session.in_transaction())
            response = handler(request)
            return await response if inspect.isawaitable(response) else response

        self.monitors = MonitorRepository(session)
        self.checks = CheckRepository(session)
        self.monitor_service = MonitorService(self.monitors)
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(recording))
        self.now = NOW
        self.service = CheckService(
            self.monitor_service, self.checks, self.client, clock=lambda: self.now
        )

    async def add_monitor(
        self, url: str = "https://a.test", timeout: float = 10.0
    ) -> Monitor:
        return await self.monitors.create(
            owner_id=ALICE.id, url=url, interval=timedelta(minutes=5), timeout=timeout
        )


def ok_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200)


@pytest.fixture(autouse=True)
async def _owners(persist_users: Callable[..., Awaitable[None]]) -> None:
    await persist_users(ALICE, BOB, ADMIN)


@pytest.fixture
async def env(session: Session) -> AsyncIterator[Env]:
    environment = Env(ok_handler, session)
    yield environment
    await environment.client.aclose()


MakeEnv = Callable[[Handler], Awaitable[Env]]


@pytest.fixture
async def make_env(session: Session) -> AsyncIterator[MakeEnv]:
    created: list[Env] = []

    async def make(handler: Handler) -> Env:
        environment = Env(handler, session)
        created.append(environment)
        return environment

    yield make
    for environment in created:
        await environment.client.aclose()


# --- run -------------------------------------------------------------------


async def test_run_stores_and_returns_the_check(env: Env) -> None:
    monitor = await env.add_monitor()

    check = await env.service.run(ALICE, monitor.id)

    assert check.monitor_id == monitor.id
    assert check.url == "https://a.test"
    assert (check.status_code, check.error, check.ok) == (200, None, True)
    assert check.checked_at == NOW
    assert check.latency_ms >= 0
    assert await env.checks.list_recent(monitor.id, 10) == [check]


async def test_run_requests_the_monitor_url_once(env: Env) -> None:
    monitor = await env.add_monitor("https://a.test/health")

    await env.service.run(ALICE, monitor.id)

    assert [str(r.url) for r in env.requests] == ["https://a.test/health"]


async def test_run_uses_the_clock_for_checked_at(env: Env) -> None:
    monitor = await env.add_monitor()
    first = await env.service.run(ALICE, monitor.id)
    env.now = NOW + timedelta(minutes=5)
    second = await env.service.run(ALICE, monitor.id)

    assert (first.checked_at, second.checked_at) == (NOW, NOW + timedelta(minutes=5))


async def test_run_checked_at_is_timezone_aware_by_default(session: Session) -> None:
    environment = Env(ok_handler, session)
    service = CheckService(
        environment.monitor_service, environment.checks, environment.client
    )
    monitor = await environment.add_monitor()

    check = await service.run(ALICE, monitor.id)
    await environment.client.aclose()

    assert check.checked_at.tzinfo is not None
    assert check.checked_at.utcoffset() == timedelta(0)


@pytest.mark.parametrize(
    ("status", "ok"),
    [(200, True), (204, True), (301, True), (404, False), (500, False), (503, False)],
)
async def test_run_ok_follows_the_status_code(
    make_env: MakeEnv, status: int, ok: bool
) -> None:
    environment = await make_env(lambda request: httpx.Response(status))
    monitor = await environment.add_monitor()

    check = await environment.service.run(ALICE, monitor.id)
    await environment.client.aclose()

    assert (check.status_code, check.ok) == (status, ok)


async def test_run_records_network_failure_as_failed_check(make_env: MakeEnv) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    environment = await make_env(refuse)
    monitor = await environment.add_monitor()

    check = await environment.service.run(ALICE, monitor.id)
    await environment.client.aclose()

    assert check.status_code is None
    assert check.ok is False
    assert check.error == "Network Error: ConnectError"
    assert await environment.checks.list_recent(monitor.id, 10) == [check]


async def test_run_passes_monitor_timeout_to_the_request(env: Env) -> None:
    monitor = await env.add_monitor(timeout=2.5)

    await env.service.run(ALICE, monitor.id)

    assert env.requests[0].extensions["timeout"]["read"] == 2.5


async def test_run_unknown_monitor_raises_and_makes_no_request(env: Env) -> None:
    with pytest.raises(MonitorNotFoundError):
        await env.service.run(ALICE, UNKNOWN_ID)

    assert env.requests == []


async def test_check_keeps_the_url_that_was_checked(env: Env) -> None:
    monitor = await env.add_monitor("https://old.test")
    old = await env.service.run(ALICE, monitor.id)
    await env.monitors.update(monitor.id, url="https://new.test")
    new = await env.service.run(ALICE, monitor.id)

    assert (old.url, new.url) == ("https://old.test", "https://new.test")
    assert (await env.checks.list_recent(monitor.id, 10))[1].url == "https://old.test"


async def test_monitor_deleted_during_request_stores_nothing(
    make_env: MakeEnv,
) -> None:
    monitor_id = 0

    async def delete_while_in_flight(request: httpx.Request) -> httpx.Response:
        await environment.monitors.delete(monitor_id)
        return httpx.Response(200)

    environment = await make_env(delete_while_in_flight)
    monitor_id = (await environment.add_monitor()).id

    with pytest.raises(MonitorNotFoundError):
        await environment.service.run(ALICE, monitor_id)

    assert await environment.checks.list_recent(monitor_id, 10) == []


async def test_database_transaction_is_not_held_while_waiting_for_the_site(
    env: Env,
) -> None:
    # Иначе медленный сайт держит соединение с базой все 10+ секунд таймаута.
    monitor = await env.add_monitor()

    await env.service.run(ALICE, monitor.id)

    assert env.transaction_open_during_request == [False]


async def test_invalid_url_error_becomes_a_failed_check(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(
        client: httpx.AsyncClient, url: str, **kwargs: object
    ) -> CheckResult:
        raise ValueError("bad url")

    monkeypatch.setattr("pulse.services.checks.check_url", broken)
    monitor = await env.add_monitor()

    check = await env.service.run(ALICE, monitor.id)

    assert check.status_code is None
    assert check.ok is False
    assert check.error == "Invalid URL: bad url"
    assert check.latency_ms == 0.0


# --- list_recent -----------------------------------------------------------


async def test_list_recent_returns_newest_first_with_limit(env: Env) -> None:
    monitor = await env.add_monitor()
    checks: list[Check] = [await env.service.run(ALICE, monitor.id) for _ in range(4)]

    assert await env.service.list_recent(ALICE, monitor.id, 2) == [checks[3], checks[2]]


async def test_list_recent_of_new_monitor_is_empty(env: Env) -> None:
    monitor = await env.add_monitor()

    assert await env.service.list_recent(ALICE, monitor.id, 10) == []


async def test_list_recent_unknown_monitor_raises(env: Env) -> None:
    with pytest.raises(MonitorNotFoundError):
        await env.service.list_recent(ALICE, UNKNOWN_ID, 10)


async def test_histories_of_different_monitors_are_separate(env: Env) -> None:
    first = await env.add_monitor("https://a.test")
    second = await env.add_monitor("https://b.test")
    check = await env.service.run(ALICE, first.id)

    assert await env.service.list_recent(ALICE, first.id, 10) == [check]
    assert await env.service.list_recent(ALICE, second.id, 10) == []


# --- владение --------------------------------------------------------------


async def test_run_on_foreign_monitor_is_not_found_and_makes_no_request(
    env: Env,
) -> None:
    monitor = await env.add_monitor()  # владелец Alice

    with pytest.raises(MonitorNotFoundError):
        await env.service.run(BOB, monitor.id)

    assert env.requests == []
    assert await env.checks.list_recent(monitor.id, 10) == []


async def test_list_recent_of_foreign_monitor_is_not_found(env: Env) -> None:
    monitor = await env.add_monitor()
    await env.service.run(ALICE, monitor.id)

    with pytest.raises(MonitorNotFoundError):
        await env.service.list_recent(BOB, monitor.id, 10)


async def test_admin_can_run_and_read_checks_of_any_monitor(env: Env) -> None:
    monitor = await env.add_monitor()

    check = await env.service.run(ADMIN, monitor.id)

    assert check.monitor_id == monitor.id
    assert await env.service.list_recent(ADMIN, monitor.id, 10) == [check]
