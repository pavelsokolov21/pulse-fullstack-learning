from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest

from pulse.domain.checks import Check
from pulse.domain.notifications import (
    DeliveryOutcome,
    FailureNotification,
    PermanentDeliveryError,
    RetryableDeliveryError,
)
from pulse.domain.users import Role, User
from pulse.repositories.checks import CheckRepository
from pulse.repositories.database import Session
from pulse.repositories.monitors import MonitorRepository
from pulse.services.notifications import NotificationService

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
OWNER = User(1, "owner@example.com", "h", Role.MANAGER)
MISSING_ID = 2_000_000_000


class FakeNotifier:
    def __init__(self) -> None:
        self.sent: list[FailureNotification] = []
        self.error: Exception | None = None

    async def send(self, notification: FailureNotification) -> None:
        if self.error is not None:
            raise self.error
        self.sent.append(notification)


class FakeLedger:
    def __init__(self) -> None:
        self.done: set[int] = set()
        self.marks: list[int] = []

    async def was_sent(self, check_id: int) -> bool:
        return check_id in self.done

    async def mark_sent(self, check_id: int) -> None:
        self.marks.append(check_id)
        self.done.add(check_id)


class Env:
    def __init__(self, session: Session) -> None:
        self.checks = CheckRepository(session)
        self.notifier = FakeNotifier()
        self.ledger = FakeLedger()
        self.service = NotificationService(self.checks, self.notifier, self.ledger)
        self.monitor_id = 0

    async def add_check(self, status: int | None = 503) -> Check:
        return await self.checks.add(
            monitor_id=self.monitor_id,
            url="https://a.test",
            status_code=status,
            latency_ms=12.0,
            error=None if status is not None else "Network Error: ConnectError",
            checked_at=NOW - timedelta(minutes=1),
        )


@pytest.fixture
async def env(session: Session, persist_users: Callable[..., Awaitable[None]]) -> Env:
    await persist_users(OWNER)
    environment = Env(session)
    monitor = await MonitorRepository(session).create(
        owner_id=OWNER.id,
        url="https://a.test",
        interval=timedelta(minutes=5),
        timeout=10,
    )
    environment.monitor_id = monitor.id
    return environment


async def test_failed_check_is_sent_once_and_recorded(env: Env) -> None:
    check = await env.add_check(503)

    outcome = await env.service.notify_check_failed(check.id)

    assert outcome is DeliveryOutcome.SENT
    assert [n.check_id for n in env.notifier.sent] == [check.id]
    assert env.notifier.sent[0].status_code == 503
    assert env.ledger.marks == [check.id]


async def test_network_failure_carries_the_error_text(env: Env) -> None:
    check = await env.add_check(None)

    await env.service.notify_check_failed(check.id)

    assert env.notifier.sent[0].error == "Network Error: ConnectError"


async def test_repeated_delivery_of_the_same_task_sends_nothing_twice(
    env: Env,
) -> None:
    check = await env.add_check()
    await env.service.notify_check_failed(check.id)

    outcome = await env.service.notify_check_failed(check.id)

    assert outcome is DeliveryOutcome.DUPLICATE
    assert len(env.notifier.sent) == 1


async def test_missing_check_is_skipped_not_failed(env: Env) -> None:
    outcome = await env.service.notify_check_failed(MISSING_ID)

    assert outcome is DeliveryOutcome.SKIPPED
    assert env.notifier.sent == []


async def test_successful_check_is_not_reported(env: Env) -> None:
    check = await env.add_check(200)

    outcome = await env.service.notify_check_failed(check.id)

    assert outcome is DeliveryOutcome.SKIPPED
    assert env.notifier.sent == []


async def test_failed_delivery_is_not_recorded_so_the_retry_can_send(
    env: Env,
) -> None:
    check = await env.add_check()
    env.notifier.error = RetryableDeliveryError("503")

    with pytest.raises(RetryableDeliveryError):
        await env.service.notify_check_failed(check.id)
    assert env.ledger.marks == []

    env.notifier.error = None
    assert await env.service.notify_check_failed(check.id) is DeliveryOutcome.SENT


async def test_permanent_error_is_not_recorded_either(env: Env) -> None:
    check = await env.add_check()
    env.notifier.error = PermanentDeliveryError("403")

    with pytest.raises(PermanentDeliveryError):
        await env.service.notify_check_failed(check.id)

    assert env.ledger.marks == []
