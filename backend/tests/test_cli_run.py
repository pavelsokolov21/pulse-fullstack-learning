import asyncio
import importlib
import tomllib
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import typer

from pulse.cli import create_client, run_checks
from pulse.settings import Settings

PYPROJECT = Path(__file__).parents[1] / "pyproject.toml"


def client_with(handler: httpx.MockTransport) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=handler)


@pytest.fixture
async def created_client() -> AsyncIterator[httpx.AsyncClient]:
    # Асинхронная фикстура: закрытие клиента (await) выполняется после теста.
    client = create_client()
    yield client
    await client.aclose()


@pytest.fixture
async def ok_client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.MockTransport(lambda request: httpx.Response(200))
    async with httpx.AsyncClient(transport=transport) as client:
        yield client


# --- create_client ---------------------------------------------------------


async def test_create_client_returns_open_async_client(
    created_client: httpx.AsyncClient,
) -> None:
    assert isinstance(created_client, httpx.AsyncClient)
    assert not created_client.is_closed


async def test_create_client_returns_a_new_client_every_time(
    created_client: httpx.AsyncClient,
) -> None:
    other = create_client()

    try:
        assert other is not created_client
    finally:
        await other.aclose()


# --- run_checks ------------------------------------------------------------


async def test_run_checks_returns_results_in_input_order(
    ok_client: httpx.AsyncClient,
) -> None:
    urls = ["https://a.test", "https://b.test", "https://c.test"]

    results = await run_checks(urls, Settings(), ok_client)

    assert [r.url for r in results] == urls
    assert all(r.ok for r in results)


async def test_run_checks_without_urls_returns_empty_list(
    ok_client: httpx.AsyncClient,
) -> None:
    assert await run_checks([], Settings(), ok_client) == []


async def test_run_checks_limits_parallel_requests_by_settings() -> None:
    in_flight = 0
    peak = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return httpx.Response(200)

    urls = [f"https://site{i}.test" for i in range(6)]

    async with client_with(httpx.MockTransport(handler)) as client:
        await run_checks(urls, Settings(concurrency=2), client)

    assert peak == 2


@pytest.mark.parametrize(("retries", "attempts"), [(0, 1), (1, 2), (3, 4)])
async def test_run_checks_retries_network_errors_by_settings(
    retries: int, attempts: int
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("refused", request=request)

    settings = Settings(retries=retries, retry_delay=0)

    async with client_with(httpx.MockTransport(handler)) as client:
        results = await run_checks(["https://a.test"], settings, client)

    assert calls == attempts
    assert results[0].error == "Network Error: ConnectError"


async def test_run_checks_waits_retry_delay_between_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pauses: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        pauses.append(seconds)

    monkeypatch.setattr("pulse.checks.runner.asyncio.sleep", fake_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    settings = Settings(retries=2, retry_delay=1.5)

    async with client_with(httpx.MockTransport(handler)) as client:
        await run_checks(["https://a.test"], settings, client)

    assert pauses == [1.5, 1.5]


async def test_run_checks_does_not_retry_http_error_statuses() -> None:
    # Ответ 503 это ответ сервера, а не сбой сети: повтор не нужен.
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503)

    async with client_with(httpx.MockTransport(handler)) as client:
        results = await run_checks(["https://a.test"], Settings(), client)

    assert calls == 1
    assert results[0].status_code == 503


async def test_run_checks_reports_invalid_url_without_request() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200)

    async with client_with(httpx.MockTransport(handler)) as client:
        results = await run_checks(["ftp://a.test"], Settings(), client)

    assert calls == 0
    assert results[0].error is not None
    assert results[0].error.startswith("Invalid URL")


# --- точка входа -----------------------------------------------------------


def test_script_entry_point_points_to_the_typer_app() -> None:
    with PYPROJECT.open("rb") as file:
        target = tomllib.load(file)["project"]["scripts"]["pulse"]

    module_name, _, attribute = target.partition(":")

    assert isinstance(
        getattr(importlib.import_module(module_name), attribute), typer.Typer
    )
