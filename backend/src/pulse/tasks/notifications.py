import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from celery import Task, shared_task

from pulse.domain.notifications import (
    DeliveryOutcome,
    PermanentDeliveryError,
    RetryableDeliveryError,
    TaskEvent,
    TaskState,
)
from pulse.repositories.checks import CheckRepository
from pulse.repositories.delivery_ledger import RedisDeliveryLedger
from pulse.repositories.redis_client import create_redis
from pulse.repositories.task_journal import RedisTaskJournal
from pulse.services.notifications import NotificationService, Notifier
from pulse.services.notifiers import LogNotifier, WebhookNotifier
from pulse.tasks.backoff import backoff_delay
from pulse.tasks.names import NOTIFY_CHECK_FAILED
from pulse.tasks.resources import open_resources
from pulse.tasks.settings import get_worker_settings

logger = logging.getLogger("pulse.tasks")

MAX_RETRIES = 5  # плюс первая попытка: всего 6


async def _record(event: TaskEvent) -> None:
    redis = create_redis(get_worker_settings().broker_url.get_secret_value())
    try:
        await RedisTaskJournal(redis).record(event)
    finally:
        await redis.aclose()


def _journal(
    check_id: int,
    state: TaskState,
    attempt: int,
    *,
    detail: str | None = None,
    retry_in: float | None = None,
) -> None:
    """Пишет событие в журнал для страницы с задачами. Никогда не роняет задачу."""
    event = TaskEvent(check_id, state, datetime.now(UTC), attempt, detail, retry_in)
    try:
        asyncio.run(_record(event))
    except Exception:
        logger.warning("Не удалось записать событие задачи", exc_info=True)


class LoggedTask(Task[[int], str]):
    """Базовый класс задач: заметный лог, когда попытки кончились."""

    def on_failure(
        self,
        exc: BaseException,
        task_id: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        einfo: Any,
    ) -> None:
        # Здесь задача окончательно упала (повторы исчерпаны или ошибка без повтора).
        # У Celery нет «очереди мёртвых писем»: без этого лога сбой виден только в
        # состоянии задачи, которого у нас нет (результаты отключены).
        logger.error(
            "Задача %s[%s] провалена окончательно: args=%s, ошибка: %r",
            self.name,
            task_id,
            args,
            exc,
        )
        _journal(
            args[0],
            TaskState.FAILED,
            self.request.retries + 1,
            detail=str(exc) or type(exc).__name__,
        )


async def _deliver(check_id: int) -> DeliveryOutcome:
    settings = get_worker_settings()
    async with open_resources(settings) as resources:
        notifier: Notifier = (
            WebhookNotifier(
                resources.http, settings.notify_webhook_url.get_secret_value()
            )
            if settings.notify_webhook_url is not None
            else LogNotifier()
        )
        async with resources.session_factory() as session:
            service = NotificationService(
                CheckRepository(session), notifier, RedisDeliveryLedger(resources.redis)
            )
            return await service.notify_check_failed(check_id)


@shared_task(
    bind=True,
    base=LoggedTask,
    name=NOTIFY_CHECK_FAILED,
    max_retries=MAX_RETRIES,
)
def notify_check_failed(self: LoggedTask, check_id: int) -> str:
    """Отправляет уведомление о неудачной проверке; повторяет с растущей паузой."""
    attempt = self.request.retries + 1
    _journal(check_id, TaskState.STARTED, attempt)
    try:
        outcome = asyncio.run(_deliver(check_id))
    except RetryableDeliveryError as exc:
        delay = backoff_delay(self.request.retries, retry_after=exc.retry_after)
        logger.warning(
            "Проверка %s: попытка %s не удалась (%s), повтор через %.1f с",
            check_id,
            attempt,
            exc,
            delay,
        )
        if self.request.retries < MAX_RETRIES:
            # На последней попытке повтора не будет: событие «провал» запишет
            # on_failure, и «повтор через N с» на дорожке соврал бы.
            _journal(
                check_id, TaskState.RETRY, attempt, detail=str(exc), retry_in=delay
            )
        # retry() бросает исключение Retry: текущее выполнение прерывается, а в
        # брокер уходит новое сообщение с задержкой. Когда повторы кончились, он
        # бросает исходную ошибку, и задача считается проваленной (on_failure).
        raise self.retry(exc=exc, countdown=delay) from exc
    except PermanentDeliveryError as exc:
        # Повтор не поможет (вебхук отказал 4xx). Подтверждаем сообщение без повторов.
        logger.error("Проверка %s: доставка невозможна: %s", check_id, exc)
        _journal(
            check_id,
            TaskState.FAILED,
            attempt,
            detail=f"{exc} (повтор не поможет)",
        )
        return "failed_permanently"
    logger.info("Проверка %s: %s (попытка %s)", check_id, outcome.value, attempt)
    _journal(check_id, TaskState(outcome.value), attempt)
    return outcome.value
