import logging

import httpx

from pulse.domain.notifications import (
    FailureNotification,
    PermanentDeliveryError,
    RetryableDeliveryError,
)

logger = logging.getLogger("pulse.notifications")

# 408 и 429 это «приходите позже», остальные 4xx это «вы прислали что-то не то».
RETRYABLE_CLIENT_ERRORS = {408, 429}


def _retry_after(response: httpx.Response) -> float | None:
    """Секунды из Retry-After. Дату в этом заголовке не разбираем: нужны секунды."""
    value = response.headers.get("Retry-After")
    if value is None or not value.isdigit():
        return None
    return float(value)


class WebhookNotifier:
    """Отправляет уведомление POST-запросом JSON на адрес вебхука (Slack, свой сервис).

    Адрес вебхука это секрет (по нему может писать любой): в логи он не попадает.
    """

    def __init__(self, client: httpx.AsyncClient, url: str) -> None:
        self._client = client
        self._url = url

    async def send(self, notification: FailureNotification) -> None:
        payload = {
            "text": f"Монитор недоступен: {notification.url} ({notification.reason})",
            "monitor_id": notification.monitor_id,
            "check_id": notification.check_id,
            "checked_at": notification.checked_at.isoformat(),
            # Получатель может сам отсеивать дубли по этому ключу.
            "idempotency_key": f"check-{notification.check_id}",
        }
        try:
            response = await self._client.post(self._url, json=payload)
        except httpx.TransportError as exc:
            # Таймаут, обрыв, DNS: сервер, возможно, жив, а сеть моргнула.
            raise RetryableDeliveryError(f"сеть: {type(exc).__name__}") from exc
        status = response.status_code
        if response.is_success:
            return
        if status >= 500 or status in RETRYABLE_CLIENT_ERRORS:
            raise RetryableDeliveryError(
                f"вебхук ответил {status}", retry_after=_retry_after(response)
            )
        raise PermanentDeliveryError(f"вебхук отказал: {status}")


class LogNotifier:
    """Заглушка без вебхука: пишет в лог. Удобно в разработке."""

    async def send(self, notification: FailureNotification) -> None:
        logger.warning(
            "УВЕДОМЛЕНИЕ: %s недоступен (%s), проверка %s",
            notification.url,
            notification.reason,
            notification.check_id,
        )
