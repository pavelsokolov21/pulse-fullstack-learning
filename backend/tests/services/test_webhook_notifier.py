import json
from datetime import UTC, datetime

import httpx
import pytest

from pulse.domain.notifications import (
    FailureNotification,
    PermanentDeliveryError,
    RetryableDeliveryError,
)
from pulse.services.notifiers import WebhookNotifier

URL = "https://hooks.example/abc"
NOTIFICATION = FailureNotification(
    check_id=7,
    monitor_id=3,
    url="https://a.test",
    status_code=503,
    error=None,
    checked_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
)


def notifier(handler: httpx.MockTransport) -> tuple[WebhookNotifier, httpx.AsyncClient]:
    client = httpx.AsyncClient(transport=handler)
    return WebhookNotifier(client, URL), client


async def send_with(status: int, headers: dict[str, str] | None = None) -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(status, headers=headers))
    hook, client = notifier(transport)
    try:
        await hook.send(NOTIFICATION)
    finally:
        await client.aclose()


async def test_posts_json_with_an_idempotency_key() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    hook, client = notifier(httpx.MockTransport(handler))
    await hook.send(NOTIFICATION)
    await client.aclose()

    body = json.loads(seen[0].content)
    assert (str(seen[0].url), seen[0].method) == (URL, "POST")
    assert body["idempotency_key"] == "check-7"
    assert body["check_id"] == 7
    assert "https://a.test" in body["text"]
    assert "HTTP 503" in body["text"]


async def test_error_text_is_used_when_there_is_no_status() -> None:
    failure = FailureNotification(
        7,
        3,
        "https://a.test",
        None,
        "Network Error: ConnectError",
        NOTIFICATION.checked_at,
    )

    assert failure.reason == "Network Error: ConnectError"


@pytest.mark.parametrize("status", [200, 201, 204])
async def test_success_statuses_pass(status: int) -> None:
    await send_with(status)


@pytest.mark.parametrize("status", [500, 502, 503, 408, 429])
async def test_server_errors_and_throttling_are_retryable(status: int) -> None:
    with pytest.raises(RetryableDeliveryError):
        await send_with(status)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 410])
async def test_client_errors_are_permanent(status: int) -> None:
    with pytest.raises(PermanentDeliveryError):
        await send_with(status)


async def test_retry_after_seconds_are_passed_on() -> None:
    with pytest.raises(RetryableDeliveryError) as info:
        await send_with(429, {"Retry-After": "42"})

    assert info.value.retry_after == 42.0


async def test_retry_after_in_http_date_form_is_ignored() -> None:
    with pytest.raises(RetryableDeliveryError) as info:
        await send_with(503, {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})

    assert info.value.retry_after is None


async def test_network_failure_is_retryable() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    hook, client = notifier(httpx.MockTransport(refuse))
    with pytest.raises(RetryableDeliveryError, match="ConnectError"):
        await hook.send(NOTIFICATION)
    await client.aclose()


async def test_webhook_url_never_appears_in_the_error() -> None:
    with pytest.raises(RetryableDeliveryError) as info:
        await send_with(500)

    assert URL not in str(info.value)
