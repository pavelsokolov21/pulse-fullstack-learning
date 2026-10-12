from collections.abc import Callable
from types import SimpleNamespace

import httpx
import pytest

from pulse.checks import runner
from pulse.checks.runner import check_url

URL = "https://example.com"

Handler = Callable[[httpx.Request], httpx.Response]


def make_client(handler: Handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def respond(status_code: int) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code)

    return handler


def raise_transport_error(exc_type: type[httpx.TransportError]) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc_type("boom", request=request)

    return handler


def redirect_then_ok(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/old":
        return httpx.Response(301, headers={"Location": "https://example.com/new"})
    return httpx.Response(200)


async def test_check_url_success() -> None:
    async with make_client(respond(200)) as client:
        result = await check_url(client, URL)

    assert result.url == URL
    assert result.status_code == 200
    assert result.error is None
    assert result.ok is True


@pytest.mark.parametrize("status_code", [200, 204, 300, 301, 304])
async def test_check_url_healthy_statuses(status_code: int) -> None:
    async with make_client(respond(status_code)) as client:
        result = await check_url(client, URL)

    assert result.status_code == status_code
    assert result.error is None
    assert result.ok is True


@pytest.mark.parametrize("status_code", [404, 500, 503])
async def test_check_url_unhealthy_statuses(status_code: int) -> None:
    async with make_client(respond(status_code)) as client:
        result = await check_url(client, URL)

    assert result.status_code == status_code
    assert result.error is None
    assert result.ok is False


async def test_check_url_follows_redirects() -> None:
    # Единственный тест с настоящим редиректом: без follow_redirects=True
    # status_code был бы 301, а не 200.
    async with make_client(redirect_then_ok) as client:
        result = await check_url(client, "https://example.com/old")

    assert result.url == "https://example.com/old"
    assert result.status_code == 200
    assert result.error is None
    assert result.ok is True


@pytest.mark.parametrize(
    "exc_type",
    [httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout],
)
async def test_check_url_network_error(exc_type: type[httpx.TransportError]) -> None:
    async with make_client(raise_transport_error(exc_type)) as client:
        result = await check_url(client, URL)

    assert result.status_code is None
    assert result.error == f"Network Error: {exc_type.__name__}"
    assert result.ok is False


async def test_check_url_does_not_swallow_unexpected_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise RuntimeError("bug in handler")

    async with make_client(handler) as client:
        with pytest.raises(RuntimeError, match="bug in handler"):
            await check_url(client, URL)


@pytest.mark.parametrize(
    "handler",
    [respond(200), raise_transport_error(httpx.ConnectError)],
    ids=["response", "network-error"],
)
async def test_check_url_measures_latency_in_milliseconds(
    monkeypatch: pytest.MonkeyPatch, handler: Handler
) -> None:
    ticks = iter([1.0, 1.25])
    monkeypatch.setattr(
        runner, "time", SimpleNamespace(perf_counter=lambda: next(ticks))
    )

    async with make_client(handler) as client:
        result = await check_url(client, URL)

    assert result.latency_ms == 250.0


@pytest.mark.parametrize(
    "handler",
    [
        respond(200),
        respond(301),
        respond(404),
        respond(503),
        raise_transport_error(httpx.ConnectError),
        raise_transport_error(httpx.ReadTimeout),
    ],
)
async def test_check_url_status_xor_error(handler: Handler) -> None:
    async with make_client(handler) as client:
        result = await check_url(client, URL)

    assert (result.status_code is None) == (result.error is not None)
