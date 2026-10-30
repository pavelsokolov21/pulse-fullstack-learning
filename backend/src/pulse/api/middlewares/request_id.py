"""ASGI-middleware идентификатора запроса.

Чистый ASGI без зависимости от FastAPI и Starlette. `BaseHTTPMiddleware` из
Starlette не используем: он ломает передачу contextvars и буферизует
потоковые ответы. Сама переменная с id и фильтр логов лежат в `core`.
"""

import re
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from pulse.core.request_context import new_request_id, request_id_var

type Scope = MutableMapping[str, Any]
type Message = MutableMapping[str, Any]
type Receive = Callable[[], Awaitable[Message]]
type Send = Callable[[Message], Awaitable[None]]
type ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

REQUEST_ID_HEADER = "X-Request-ID"
_HEADER_NAME = REQUEST_ID_HEADER.lower().encode("latin-1")

# Входящий id попадает в логи и в заголовок ответа, поэтому принимаем только
# безопасный алфавит и длину: иначе возможна подмена строк лога (log injection).
_VALID_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")


def _incoming_request_id(scope: Scope) -> str | None:
    for name, value in scope.get("headers", []):
        if name.lower() == _HEADER_NAME:
            candidate = value.decode("latin-1")
            return candidate if _VALID_ID.fullmatch(candidate) else None
    return None


class RequestIdMiddleware:
    """Берёт id из `X-Request-ID` (если он безопасен) или создаёт новый.

    Id доступен через `get_request_id()` и `scope["state"]["request_id"]`
    (то есть `request.state.request_id`) и возвращается в заголовке ответа.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _incoming_request_id(scope) or new_request_id()
        # Копия в scope нужна обработчикам, которые вызываются снаружи
        # middleware (ответ 500 строит ServerErrorMiddleware уже после reset).
        scope.setdefault("state", {})["request_id"] = request_id
        token = request_id_var.set(request_id)

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if name.lower() != _HEADER_NAME
                ]
                headers.append((_HEADER_NAME, request_id.encode("latin-1")))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            request_id_var.reset(token)
