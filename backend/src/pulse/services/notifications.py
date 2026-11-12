import logging
from typing import Protocol

from pulse.domain.checks import Check
from pulse.domain.notifications import DeliveryOutcome, FailureNotification
from pulse.repositories.checks import CheckRepository

logger = logging.getLogger("pulse.notifications")


class Notifier(Protocol):
    """Канал доставки. Бросает `RetryableDeliveryError` или `PermanentDeliveryError`."""

    async def send(self, notification: FailureNotification) -> None: ...


class DeliveryLedger(Protocol):
    async def was_sent(self, check_id: int) -> bool: ...

    async def mark_sent(self, check_id: int) -> None: ...


def _to_notification(check: Check) -> FailureNotification:
    return FailureNotification(
        check_id=check.id,
        monitor_id=check.monitor_id,
        url=check.url,
        status_code=check.status_code,
        error=check.error,
        checked_at=check.checked_at,
    )


class NotificationService:
    """Сообщает о неудачной проверке. Логика задачи Celery, но без Celery.

    Задача в `pulse.tasks` только вызывает этот сервис и решает, повторять ли. Так
    правило «что отправлять и когда пропустить» проверяется обычными тестами, без
    брокера и воркера.
    """

    def __init__(
        self, checks: CheckRepository, notifier: Notifier, ledger: DeliveryLedger
    ) -> None:
        self._checks = checks
        self._notifier = notifier
        self._ledger = ledger

    async def notify_check_failed(self, check_id: int) -> DeliveryOutcome:
        check = await self._checks.get(check_id)
        if check is None or check.ok:
            # Проверку успели удалить (монитор снесли или чистка старых записей).
            # Повтор не поможет, это не ошибка.
            logger.info(
                "Уведомление пропущено: проверки %s нет или она успешна", check_id
            )
            return DeliveryOutcome.SKIPPED
        if await self._ledger.was_sent(check_id):
            logger.info("Уведомление по проверке %s уже отправлено", check_id)
            return DeliveryOutcome.DUPLICATE
        await self._notifier.send(_to_notification(check))
        await self._ledger.mark_sent(check_id)
        return DeliveryOutcome.SENT
