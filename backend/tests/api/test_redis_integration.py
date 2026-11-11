"""Redis в приложении: запуск, настройки, общий лимит для нескольких приложений."""

import re
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from redis.exceptions import RedisError

from pulse.api.main import create_app
from pulse.api.settings import ApiSettings

PASSWORD = "wrong password"


def settings_with(**overrides: Any) -> ApiSettings:
    return ApiSettings(**overrides)


def login(client: TestClient):
    return client.post(
        "/auth/login", data={"username": "nobody@example.com", "password": PASSWORD}
    )


# --- запуск ----------------------------------------------------------------


def test_startup_pings_redis_and_exposes_one_client() -> None:
    with TestClient(create_app()) as client:
        redis = client.app.state.redis  # type: ignore[attr-defined]
        assert redis is client.app.state.redis  # type: ignore[attr-defined]
        assert client.app.state.report_cache is not None  # type: ignore[attr-defined]


def test_startup_fails_when_redis_is_unreachable() -> None:
    app = create_app(settings_with(redis_url="redis://:x@127.0.0.1:1/0"))

    with pytest.raises((RedisError, OSError)), TestClient(app):
        pass


def test_startup_fails_with_a_wrong_redis_password() -> None:
    import os

    wrong = re.sub(r":[^:@]*@", ":wrong-password@", os.environ["PULSE_API_REDIS_URL"])
    app = create_app(settings_with(redis_url=wrong))

    with pytest.raises(RedisError), TestClient(app):
        pass


def test_failed_startup_does_not_leak_the_database_pool() -> None:
    # После неудачного запуска повторный запуск обычного приложения работает.
    app = create_app(settings_with(redis_url="redis://:x@127.0.0.1:1/0"))
    with pytest.raises((RedisError, OSError)), TestClient(app):
        pass

    with TestClient(create_app()) as client:
        assert client.get("/openapi.json").status_code == 200


# --- настройки -------------------------------------------------------------


def test_redis_url_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PULSE_API_REDIS_URL")

    with pytest.raises(ValidationError, match="redis_url"):
        ApiSettings(**dict[str, Any]())


@pytest.mark.parametrize(
    "url", ["", "http://localhost:6379", "localhost:6379", "postgresql://x"]
)
def test_redis_url_must_be_a_redis_address(url: str) -> None:
    with pytest.raises(ValidationError, match="redis"):
        settings_with(redis_url=url)


@pytest.mark.parametrize("url", ["redis://localhost:6379/0", "rediss://:p@h:6380/1"])
def test_redis_and_tls_redis_addresses_are_accepted(url: str) -> None:
    assert settings_with(redis_url=url).redis_url.get_secret_value() == url


def test_redis_password_is_not_shown_in_settings_repr() -> None:
    settings = settings_with(redis_url="redis://:s3cret-pass@localhost:6379/0")

    assert "s3cret-pass" not in repr(settings)
    assert "s3cret-pass" not in str(settings)


def test_cache_ttl_defaults_to_a_minute_and_must_be_positive() -> None:
    assert settings_with().report_cache_ttl_seconds == 60
    with pytest.raises(ValidationError):
        settings_with(report_cache_ttl_seconds=0)


# --- лимит общий для приложений ---------------------------------------------


def test_login_limit_is_shared_by_two_app_instances() -> None:
    # Два приложения со своими lifespan и клиентами Redis моделируют два процесса
    # сервера. Лимит 5 на ключ (адрес клиента) один на обоих.
    with TestClient(create_app()) as first, TestClient(create_app()) as second:
        statuses = [login(first).status_code for _ in range(3)]
        statuses += [login(second).status_code for _ in range(3)]

    assert statuses == [401, 401, 401, 401, 401, 429]


def test_limit_state_lives_in_redis_not_in_the_process() -> None:
    with TestClient(create_app()) as first:
        for _ in range(5):
            login(first)

    # Приложение «перезапущено»: счётчик пережил его, потому что он в Redis.
    with TestClient(create_app()) as restarted:
        assert login(restarted).status_code == 429
