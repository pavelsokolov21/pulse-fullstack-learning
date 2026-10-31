import logging
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pulse.api import main
from pulse.api.deps import get_current_user, get_http_client
from pulse.api.main import create_app
from pulse.api.settings import ApiSettings, get_settings
from pulse.domain.users import User


def settings_with(**overrides: Any) -> ApiSettings:
    """`ApiSettings` с обязательным секретом из окружения (см. tests/conftest.py).

    Через `**kwargs`, потому что pyright считает `jwt_secret` обязательным
    аргументом конструктора, хотя BaseSettings читает его из окружения.
    """
    return ApiSettings(**overrides)


def logged_in(app: FastAPI) -> FastAPI:
    """Приложение, в котором вход подменён: тесты ниже не про авторизацию."""
    user = User(1, "tester@example.com", "hash")
    app.dependency_overrides[get_current_user] = lambda: user
    return app


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in ("ENVIRONMENT", "LOG_LEVEL", "MAX_CHECKS_PER_MONITOR"):
        monkeypatch.delenv(f"PULSE_API_{name}", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_module_level_app_is_created_by_the_factory() -> None:
    assert isinstance(main.app, FastAPI)
    assert main.app.state.settings is not None


# --- environment -----------------------------------------------------------


@pytest.mark.parametrize("path", ["/docs", "/redoc"])
def test_dev_serves_documentation(path: str) -> None:
    with TestClient(create_app(settings_with(environment="dev"))) as client:
        assert client.get(path).status_code == 200


@pytest.mark.parametrize("path", ["/docs", "/redoc"])
def test_prod_hides_documentation(path: str) -> None:
    with TestClient(create_app(settings_with(environment="prod"))) as client:
        response = client.get(path)

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_prod_keeps_the_api_itself_and_openapi_schema() -> None:
    app = logged_in(create_app(settings_with(environment="prod")))

    with TestClient(app) as client:
        assert client.get("/monitors").status_code == 200
        assert client.get("/openapi.json").status_code == 200


def test_app_keeps_the_settings_it_was_built_with() -> None:
    settings = settings_with(environment="prod", max_checks_per_monitor=7)

    assert create_app(settings).state.settings is settings


def test_apps_do_not_share_state() -> None:
    first = TestClient(logged_in(create_app(settings_with())))
    second = TestClient(logged_in(create_app(settings_with())))

    with first, second:
        first.post("/monitors", json={"url": "a.test", "interval": "5m"})

        assert len(first.get("/monitors").json()) == 1
        assert second.get("/monitors").json() == []


def test_factory_without_arguments_reads_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PULSE_API_ENVIRONMENT", "prod")

    with TestClient(create_app()) as client:
        assert client.get("/docs").status_code == 404


def test_invalid_environment_fails_at_app_creation_not_at_first_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PULSE_API_LOG_LEVEL", "VERBOSE")

    with pytest.raises(Exception, match="log_level"):
        create_app()


# --- max_checks_per_monitor ------------------------------------------------


def test_check_history_cap_comes_from_settings() -> None:
    web = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200))
    )
    app = logged_in(create_app(settings_with(max_checks_per_monitor=3)))
    app.dependency_overrides[get_http_client] = lambda: web

    with TestClient(app) as client:
        monitor_id = client.post(
            "/monitors", json={"url": "a.test", "interval": "5m"}
        ).json()["id"]
        for _ in range(5):
            client.post(f"/monitors/{monitor_id}/checks")

        listed = client.get(f"/monitors/{monitor_id}/checks").json()

    assert [c["id"] for c in listed] == [5, 4, 3]


# --- log_level -------------------------------------------------------------


def test_log_level_is_applied_while_the_app_runs_and_restored_after() -> None:
    logger = logging.getLogger("pulse")
    before = logger.level

    with TestClient(create_app(settings_with(log_level="DEBUG"))):
        assert logger.level == logging.DEBUG

    assert logger.level == before


def test_log_level_is_restored_even_if_startup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = logging.getLogger("pulse")
    before = logger.level

    def broken() -> httpx.AsyncClient:
        raise RuntimeError("no client")

    monkeypatch.setattr("pulse.api.main.create_http_client", broken)

    with (
        pytest.raises(RuntimeError),
        TestClient(create_app(settings_with(log_level="ERROR"))),
    ):
        pass

    assert logger.level == before


# --- секрет JWT ------------------------------------------------------------


def test_app_refuses_to_start_without_jwt_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PULSE_API_JWT_SECRET")

    with pytest.raises(Exception, match="jwt_secret"):
        create_app()


def test_token_lifetime_comes_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PULSE_API_ACCESS_TOKEN_TTL_MINUTES", "30")
    app = create_app()

    with TestClient(app) as client:
        client.post(
            "/auth/register", json={"email": "a@example.com", "password": "password1"}
        )
        response = client.post(
            "/auth/login", data={"username": "a@example.com", "password": "password1"}
        )

    assert response.json()["expires_in"] == 1800
