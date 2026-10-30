import asyncio

import httpx
import pytest

from pulse.api.middlewares.request_id import (
    REQUEST_ID_HEADER,
    ASGIApp,
    Message,
    Receive,
    RequestIdMiddleware,
    Scope,
    Send,
)
from pulse.core.request_context import get_request_id

HEX_ID = r"[0-9a-f]{32}"


class Probe:
    """ASGI-приложение, которое запоминает, что видело внутри запроса."""

    def __init__(self, response_headers: list[tuple[bytes, bytes]] | None = None):
        self.seen_context_id: list[str | None] = []
        self.seen_state_id: list[str | None] = []
        self.response_headers = response_headers or []
        self.delay = 0.0

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await asyncio.sleep(self.delay)
        self.seen_context_id.append(get_request_id())
        self.seen_state_id.append(scope.get("state", {}).get("request_id"))
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/plain"), *self.response_headers],
            }
        )
        await send({"type": "http.response.body", "body": b"ok"})


def client_for(app: ASGIApp) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=RequestIdMiddleware(app)),
        base_url="http://test",
    )


# --- создание и возврат id -------------------------------------------------


async def test_generates_id_when_header_is_missing() -> None:
    async with client_for(Probe()) as client:
        response = await client.get("/")

    assert response.headers[REQUEST_ID_HEADER].isalnum()
    assert len(response.headers[REQUEST_ID_HEADER]) == 32


async def test_generated_ids_are_unique() -> None:
    async with client_for(Probe()) as client:
        ids = {(await client.get("/")).headers[REQUEST_ID_HEADER] for _ in range(20)}

    assert len(ids) == 20


@pytest.mark.parametrize("incoming", ["abc-123", "req_1.2", "A" * 64, "7"])
async def test_valid_incoming_id_is_kept(incoming: str) -> None:
    async with client_for(Probe()) as client:
        response = await client.get("/", headers={REQUEST_ID_HEADER: incoming})

    assert response.headers[REQUEST_ID_HEADER] == incoming


@pytest.mark.parametrize(
    "incoming",
    ["", "a b", "A" * 65, "<script>", "id;drop", "кириллица".encode(), "a/b", "a\tb"],
    ids=[
        "empty",
        "space",
        "too-long",
        "html",
        "semicolon",
        "non-ascii",
        "slash",
        "tab",
    ],
)
async def test_unsafe_incoming_id_is_replaced(incoming: str | bytes) -> None:
    raw = incoming.encode() if isinstance(incoming, str) else incoming

    async with client_for(Probe()) as client:
        response = await client.get("/", headers=[(b"X-Request-ID", raw)])

    returned = response.headers[REQUEST_ID_HEADER]
    assert returned.encode() != raw
    assert len(returned) == 32


async def test_incoming_header_name_is_case_insensitive() -> None:
    async with client_for(Probe()) as client:
        response = await client.get("/", headers={"x-REQUEST-id": "from-client"})

    assert response.headers[REQUEST_ID_HEADER] == "from-client"


async def test_existing_response_header_is_replaced_not_duplicated() -> None:
    app = Probe(response_headers=[(b"x-request-id", b"set-by-app")])

    async with client_for(app) as client:
        response = await client.get("/", headers={REQUEST_ID_HEADER: "from-client"})

    assert response.headers.get_list(REQUEST_ID_HEADER) == ["from-client"]


async def test_other_response_headers_are_preserved() -> None:
    async with client_for(Probe()) as client:
        response = await client.get("/")

    assert response.headers["content-type"] == "text/plain"
    assert response.text == "ok"


# --- контекст --------------------------------------------------------------


async def test_id_is_visible_inside_the_app_in_context_and_scope() -> None:
    app = Probe()

    async with client_for(app) as client:
        response = await client.get("/")

    returned = response.headers[REQUEST_ID_HEADER]
    assert app.seen_context_id == [returned]
    assert app.seen_state_id == [returned]


async def test_context_is_reset_after_the_request() -> None:
    async with client_for(Probe()) as client:
        await client.get("/")

    assert get_request_id() is None


async def test_context_is_reset_when_the_app_raises() -> None:
    async def failing(scope: Scope, receive: Receive, send: Send) -> None:
        raise RuntimeError("boom")

    async with client_for(failing) as client:
        with pytest.raises(RuntimeError):
            await client.get("/")

    assert get_request_id() is None


async def test_concurrent_requests_do_not_mix_ids() -> None:
    app = Probe()
    app.delay = 0.01

    async with client_for(app) as client:
        responses = await asyncio.gather(
            *(client.get("/", headers={REQUEST_ID_HEADER: f"id-{n}"}) for n in range(5))
        )

    assert sorted(i or "" for i in app.seen_context_id) == [f"id-{n}" for n in range(5)]
    assert [r.headers[REQUEST_ID_HEADER] for r in responses] == [
        f"id-{n}" for n in range(5)
    ]


async def test_non_http_scope_is_passed_through_untouched() -> None:
    calls: list[str] = []

    async def inner(scope: Scope, receive: Receive, send: Send) -> None:
        calls.append(scope["type"])
        assert get_request_id() is None
        assert "state" not in scope

    async def receive() -> Message:
        return {"type": "lifespan.startup"}

    async def send(message: Message) -> None:
        raise AssertionError("не должно быть ответа")

    await RequestIdMiddleware(inner)({"type": "lifespan"}, receive, send)

    assert calls == ["lifespan"]
