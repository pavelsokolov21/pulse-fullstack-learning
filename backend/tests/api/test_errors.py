import inspect
import logging
from collections.abc import Iterator

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from pulse.api.deps import get_store
from pulse.api.errors import register_error_handlers
from pulse.api.main import app

SECRET = "secret-path /etc/passwd"


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def failing_client() -> TestClient:
    """Отдельное приложение с маршрутами, которые падают: общий `app` не трогаем."""
    failing_app = FastAPI()
    register_error_handlers(failing_app)

    @failing_app.get("/boom")
    async def boom() -> None:  # pyright: ignore[reportUnusedFunction]
        raise RuntimeError(SECRET)

    @failing_app.get("/teapot")
    async def teapot() -> None:  # pyright: ignore[reportUnusedFunction]
        raise HTTPException(418, detail=SECRET, headers={"X-Hint": "tea"})

    # raise_server_exceptions=False: тест видит ответ 500, а не исключение.
    return TestClient(failing_app, raise_server_exceptions=False)


# --- маршрут и метод -------------------------------------------------------


def test_unknown_route_uses_unified_body(client: TestClient) -> None:
    response = client.get("/nope")

    assert response.status_code == 404
    assert response.json() == {"code": "not_found", "message": "Not Found"}


def test_wrong_method_uses_unified_body_and_keeps_allow_header(
    client: TestClient,
) -> None:
    response = client.put("/monitors")

    assert response.status_code == 405
    assert response.json() == {
        "code": "method_not_allowed",
        "message": "Method Not Allowed",
    }
    assert "POST" in response.headers["allow"]


def test_route_404_differs_from_monitor_404_by_code(client: TestClient) -> None:
    route = client.get("/nope").json()["code"]
    monitor = client.get("/monitors/999").json()["code"]

    assert (route, monitor) == ("not_found", "monitor_not_found")


def test_every_error_body_has_code_and_message(client: TestClient) -> None:
    responses = [
        client.get("/nope"),
        client.put("/monitors"),
        client.get("/monitors/999"),
        client.get("/monitors/abc"),
        client.post("/monitors", json={}),
    ]

    for response in responses:
        assert {"code", "message"} <= set(response.json())
        assert "detail" not in response.json()


def test_http_exception_detail_is_not_echoed(failing_client: TestClient) -> None:
    response = failing_client.get("/teapot")

    assert response.status_code == 418
    assert response.json() == {"code": "i'm_a_teapot", "message": "I'm a Teapot"}
    assert SECRET not in response.text
    assert response.headers["x-hint"] == "tea"


# --- необработанные исключения ---------------------------------------------


def test_unhandled_exception_returns_generic_500(failing_client: TestClient) -> None:
    response = failing_client.get("/boom")

    assert response.status_code == 500
    assert response.json() == {
        "code": "internal_error",
        "message": "Внутренняя ошибка сервера",
    }


def test_unhandled_exception_text_is_not_leaked(failing_client: TestClient) -> None:
    response = failing_client.get("/boom")

    assert SECRET not in response.text
    assert "RuntimeError" not in response.text
    assert "Traceback" not in response.text


def test_unhandled_exception_is_logged_with_traceback(
    failing_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR, logger="pulse.api.errors"):
        failing_client.get("/boom")

    [record] = caplog.records
    assert record.exc_info is not None
    assert isinstance(record.exc_info[1], RuntimeError)


def test_app_keeps_working_after_unhandled_exception(
    failing_client: TestClient,
) -> None:
    failing_client.get("/boom")

    assert failing_client.get("/boom").status_code == 500
    assert failing_client.get("/nope").status_code == 404


# --- зависимость -----------------------------------------------------------


def test_get_store_is_async_to_avoid_thread_pool() -> None:
    # Синхронная зависимость уходит в пул потоков ради одной строки.
    assert inspect.iscoroutinefunction(get_store)
