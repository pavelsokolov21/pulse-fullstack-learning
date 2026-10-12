import asyncio
import inspect
import time
from collections.abc import Callable, Coroutine
from types import SimpleNamespace

import httpx
import pytest

from pulse.checks import runner
from pulse.checks.runner import check_many, check_url

URL = "https://example.com"

# MockTransport принимает либо обычный, либо async-обработчик: это union
# вызываемых, а не одна функция с union в возвращаемом типе.
Handler = (
    Callable[[httpx.Request], httpx.Response]
    | Callable[[httpx.Request], Coroutine[None, None, httpx.Response]]
)


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


async def test_results_keep_input_order() -> None:
    # Первый URL отвечает дольше всех, последний быстрее всех:
    # порядок завершения обратный порядку входа.
    delays = {"slow.test": 0.05, "mid.test": 0.02, "fast.test": 0.0}
    urls = [f"https://{host}/" for host in delays]

    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(delays[request.url.host])
        return httpx.Response(200)

    async with make_client(handler) as client:
        results = await check_many(client, urls, concurrency=3)

    assert [r.url for r in results] == urls


@pytest.mark.parametrize("concurrency", [1, 3])
async def test_concurrency_is_limited(concurrency: int) -> None:
    urls = [f"https://site{i}.test/" for i in range(20)]
    in_flight = 0
    max_in_flight = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return httpx.Response(200)

    async with make_client(handler) as client:
        await check_many(client, urls, concurrency=concurrency)

    # Ровно concurrency, а не «не больше»: иначе тест пройдёт и на
    # последовательной реализации.
    assert max_in_flight == concurrency


async def test_one_failing_url_does_not_affect_others() -> None:
    urls = ["https://ok1.test/", "https://down.test/", "https://ok2.test/"]

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "down.test":
            raise httpx.ConnectError("boom", request=request)
        return httpx.Response(200)

    async with make_client(handler) as client:
        results = await check_many(client, urls, retries=0)

    assert [r.ok for r in results] == [True, False, True]
    assert results[1].status_code is None
    assert results[1].error is not None


async def test_network_error_is_retried_until_success() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls <= 2:
            raise httpx.ConnectError("boom", request=request)
        return httpx.Response(200)

    async with make_client(handler) as client:
        results = await check_many(client, [URL], retries=2, retry_delay=0)

    assert calls == 3
    assert results[0].ok is True


@pytest.mark.parametrize("retries", [0, 1, 3])
async def test_retries_are_bounded(retries: int) -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("boom", request=request)

    async with make_client(handler) as client:
        results = await check_many(client, [URL], retries=retries, retry_delay=0)

    assert calls == 1 + retries
    assert results[0].status_code is None
    assert results[0].error is not None


@pytest.mark.parametrize("status_code", [404, 500, 503])
async def test_http_error_status_is_not_retried(status_code: int) -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status_code)

    async with make_client(handler) as client:
        results = await check_many(client, [URL], retries=3, retry_delay=0)

    # Ответ получен, это настоящее состояние сайта, а не сбой запроса.
    assert calls == 1
    assert results[0].status_code == status_code
    assert results[0].ok is False


@pytest.mark.parametrize("concurrency", [0, -1])
async def test_invalid_concurrency_raises(concurrency: int) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    async with make_client(handler) as client:
        with pytest.raises(ValueError, match="concurrency"):
            await check_many(client, [URL], concurrency=concurrency)


async def test_retry_pause_does_not_hold_concurrency_slot() -> None:
    # concurrency=1: после сбоя "a" слот достаётся "b", а повтор "a" идёт за ним.
    # Один порядок вызовов паузу не доказывает: FIFO-очередь семафора даёт такой
    # же порядок, даже если пауза держит слот. Момент старта "b" проверяет тест
    # test_other_url_starts_while_retry_is_waiting.
    calls: list[str] = []
    a_failed_once = False

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal a_failed_once
        host = request.url.host
        calls.append(host)
        if host == "a.test" and not a_failed_once:
            a_failed_once = True
            raise httpx.ConnectError("boom", request=request)
        return httpx.Response(200)

    urls = ["https://a.test/", "https://b.test/"]

    async with make_client(handler) as client:
        await check_many(client, urls, concurrency=1, retries=1, retry_delay=0.05)

    assert calls == ["a.test", "b.test", "a.test"]


async def test_other_url_starts_while_retry_is_waiting() -> None:
    # concurrency=1, "a" падает и ждёт 0.3 с перед повтором. Если слот держится
    # на время паузы, "b" стартует только после неё (~0.3 с), иначе сразу (~0 с).
    retry_delay = 0.3
    started_at: dict[str, float] = {}
    a_failed_once = False

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal a_failed_once
        host = request.url.host
        started_at.setdefault(host, time.perf_counter() - t0)
        if host == "a.test" and not a_failed_once:
            a_failed_once = True
            raise httpx.ConnectError("boom", request=request)
        return httpx.Response(200)

    urls = ["https://a.test/", "https://b.test/"]

    async with make_client(handler) as client:
        t0 = time.perf_counter()
        results = await check_many(
            client, urls, concurrency=1, retries=1, retry_delay=retry_delay
        )

    assert [r.ok for r in results] == [True, True]
    assert started_at["b.test"] < retry_delay / 2


def test_validate_range_keeps_coroutine_function() -> None:
    assert inspect.iscoroutinefunction(check_many)


async def test_empty_urls_return_empty_list() -> None:
    async with make_client(respond(200)) as client:
        assert await check_many(client, []) == []


async def test_check_many_accepts_any_sequence() -> None:
    urls = ("https://a.test/", "https://b.test/")

    async with make_client(respond(200)) as client:
        results = await check_many(client, urls)

    assert [r.url for r in results] == list(urls)


async def test_negative_retries_raise() -> None:
    async with make_client(respond(200)) as client:
        with pytest.raises(ValueError, match="retries"):
            await check_many(client, [URL], retries=-1)


async def test_non_integer_retries_raise() -> None:
    async with make_client(respond(200)) as client:
        with pytest.raises(TypeError, match="retries"):
            # Намеренно неверный тип: валидатор должен поймать дробное значение.
            await check_many(client, [URL], retries=1.5)  # pyright: ignore[reportArgumentType]


@pytest.mark.parametrize("retry_delay", [-0.1, float("nan")])
async def test_invalid_retry_delay_raises(retry_delay: float) -> None:
    async with make_client(respond(200)) as client:
        with pytest.raises(ValueError, match="retry_delay"):
            await check_many(client, [URL], retry_delay=retry_delay)


async def test_check_url_raises_value_error_for_garbage_url() -> None:
    async with make_client(respond(200)) as client:
        with pytest.raises(ValueError, match="пуст"):
            await check_url(client, "")


async def test_check_url_adds_scheme_and_keeps_original_url() -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200)

    async with make_client(handler) as client:
        result = await check_url(client, "Example.com")

    assert requested == ["https://example.com"]
    assert result.url == "Example.com"
    assert result.ok is True


async def test_check_url_converts_httpx_invalid_url_to_value_error() -> None:
    # NUL в хосте пропускает normalize_url, но httpx отвергает его как InvalidURL.
    async with make_client(respond(200)) as client:
        with pytest.raises(ValueError, match="non-printable"):
            await check_url(client, "http://\x00.com")


async def test_garbage_url_does_not_break_batch_and_is_not_retried() -> None:
    called_hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        called_hosts.append(request.url.host)
        return httpx.Response(200)

    urls = ["https://a.test/", "", "http://\x00.com", "https://b.test/"]

    async with make_client(handler) as client:
        results = await check_many(client, urls, retries=3, retry_delay=0)

    assert [r.url for r in results] == urls
    assert [r.ok for r in results] == [True, False, False, True]
    for bad in (results[1], results[2]):
        assert bad.status_code is None
        assert bad.error is not None
        assert bad.error.startswith("Invalid URL")
    # Мусорные URL не дошли до сети, а значит и не повторялись.
    assert sorted(called_hosts) == ["a.test", "b.test"]


async def test_unexpected_error_in_one_check_is_raised_as_exception_group() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "bug.test":
            raise RuntimeError("bug in handler")
        return httpx.Response(200)

    urls = ["https://a.test/", "https://bug.test/", "https://b.test/"]

    async with make_client(handler) as client:
        with pytest.raises(ExceptionGroup) as exc_info:
            await check_many(client, urls)

    # TaskGroup оборачивает исключение в группу: `except RuntimeError` его не поймает.
    assert exc_info.group_contains(RuntimeError, match="bug in handler")
