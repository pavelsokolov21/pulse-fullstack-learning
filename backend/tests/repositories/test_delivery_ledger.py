import logging

import pytest

from pulse.domain.notifications import RetryableDeliveryError
from pulse.repositories.delivery_ledger import SENT_TTL_SECONDS, RedisDeliveryLedger
from pulse.repositories.redis_client import RedisClient, create_redis


@pytest.fixture
def ledger(redis_client: RedisClient) -> RedisDeliveryLedger:
    return RedisDeliveryLedger(redis_client)


async def test_nothing_is_sent_at_first(ledger: RedisDeliveryLedger) -> None:
    assert await ledger.was_sent(1) is False


async def test_marked_check_is_remembered(ledger: RedisDeliveryLedger) -> None:
    await ledger.mark_sent(1)

    assert await ledger.was_sent(1) is True


async def test_checks_are_independent(ledger: RedisDeliveryLedger) -> None:
    await ledger.mark_sent(1)

    assert await ledger.was_sent(2) is False


async def test_record_expires(
    ledger: RedisDeliveryLedger, redis_client: RedisClient
) -> None:
    await ledger.mark_sent(1)

    ttl = await redis_client.ttl("pulse:notify:sent:1")

    assert 0 < ttl <= SENT_TTL_SECONDS


async def test_unknown_state_is_retried_not_guessed() -> None:
    # Redis лёг: не знаем, отправляли ли. Молча слать второй раз нельзя, терять
    # уведомление тоже: пусть задача повторится позже.
    dead = create_redis("redis://:x@127.0.0.1:1/0")

    with pytest.raises(RetryableDeliveryError):
        await RedisDeliveryLedger(dead).was_sent(1)
    await dead.aclose()


async def test_failed_record_after_a_sent_message_does_not_raise(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Сообщение уже ушло: повтор задачи ради записи дал бы дубль.
    dead = create_redis("redis://:x@127.0.0.1:1/0")

    with caplog.at_level(logging.ERROR, logger="pulse.notifications"):
        await RedisDeliveryLedger(dead).mark_sent(1)
    await dead.aclose()

    assert "Не удалось записать отправку" in caplog.text
