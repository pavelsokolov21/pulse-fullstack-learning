"""Задача `notify_check_failed`: повторы, окончательный отказ, отсутствие повторов."""

import logging

import pytest

import pulse.tasks.notifications as task_module
from pulse.domain.notifications import (
    DeliveryOutcome,
    PermanentDeliveryError,
    RetryableDeliveryError,
)
from pulse.tasks.notifications import MAX_RETRIES, notify_check_failed


class Deliver:
    """Подмена доставки: ведёт журнал вызовов и бросает заданные ошибки по очереди."""

    def __init__(self, *script: Exception | DeliveryOutcome) -> None:
        self.script = list(script)
        self.calls: list[int] = []

    async def __call__(self, check_id: int) -> DeliveryOutcome:
        self.calls.append(check_id)
        step = self.script.pop(0) if self.script else DeliveryOutcome.SENT
        if isinstance(step, Exception):
            raise step
        return step


@pytest.fixture
def delays(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, float | None]]:
    """Подмена backoff: запоминает аргументы, паузы убирает (в eager-режиме их нет)."""
    seen: list[tuple[int, float | None]] = []

    def fake(retries: int, *, retry_after: float | None = None) -> float:
        seen.append((retries, retry_after))
        return 0.0

    monkeypatch.setattr(task_module, "backoff_delay", fake)
    return seen


def run(monkeypatch: pytest.MonkeyPatch, deliver: Deliver, check_id: int = 5):
    monkeypatch.setattr(task_module, "_deliver", deliver)
    return notify_check_failed.apply(args=(check_id,))


def test_success_returns_the_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    deliver = Deliver(DeliveryOutcome.SENT)

    result = run(monkeypatch, deliver)

    assert (result.state, result.result) == ("SUCCESS", "sent")
    assert deliver.calls == [5]


def test_duplicate_is_a_success_without_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = run(monkeypatch, Deliver(DeliveryOutcome.DUPLICATE))

    assert (result.state, result.result) == ("SUCCESS", "duplicate")


def test_transient_errors_are_retried_until_it_works(
    monkeypatch: pytest.MonkeyPatch, delays: list[tuple[int, float | None]]
) -> None:
    deliver = Deliver(
        RetryableDeliveryError("503"),
        RetryableDeliveryError("503"),
        DeliveryOutcome.SENT,
    )

    result = run(monkeypatch, deliver)

    assert deliver.calls == [5, 5, 5]
    assert result.state == "SUCCESS"
    # Номер попытки растёт: пауза считается от неё (экспонента).
    assert [retries for retries, _ in delays] == [0, 1]


def test_retry_after_from_the_server_reaches_the_backoff(
    monkeypatch: pytest.MonkeyPatch, delays: list[tuple[int, float | None]]
) -> None:
    deliver = Deliver(RetryableDeliveryError("429", retry_after=30.0))

    run(monkeypatch, deliver)

    assert delays[0] == (0, 30.0)


def test_retries_stop_after_the_limit_and_the_task_fails(
    monkeypatch: pytest.MonkeyPatch,
    delays: list[tuple[int, float | None]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    deliver = Deliver(*[RetryableDeliveryError("down")] * 20)

    with caplog.at_level(logging.ERROR, logger="pulse.tasks"):
        result = run(monkeypatch, deliver)

    # Пять повторов после первой попытки: шесть вызовов. Число закреплено здесь
    # намеренно: его смена меняет время, в течение которого мы бьёмся за доставку.
    assert len(deliver.calls) == 6
    assert MAX_RETRIES == 5
    assert result.state == "FAILURE"
    assert isinstance(result.result, RetryableDeliveryError)
    assert "провалена окончательно" in caplog.text


def test_permanent_error_is_not_retried(
    monkeypatch: pytest.MonkeyPatch, delays: list[tuple[int, float | None]]
) -> None:
    deliver = Deliver(PermanentDeliveryError("403"))

    result = run(monkeypatch, deliver)

    assert deliver.calls == [5]
    assert (result.state, result.result) == ("SUCCESS", "failed_permanently")
    assert delays == []


def test_unexpected_bug_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    # Ошибка в коде не пройдёт от повтора: она видна сразу, а не после пяти попыток.
    deliver = Deliver(TypeError("баг"))

    result = run(monkeypatch, deliver)

    assert deliver.calls == [5]
    assert result.state == "FAILURE"
