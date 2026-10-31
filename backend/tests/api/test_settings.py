from typing import Any

import pytest
from pydantic import ValidationError

from pulse.api.settings import ApiSettings, get_settings

NAMES = ("ENVIRONMENT", "LOG_LEVEL", "MAX_CHECKS_PER_MONITOR")


def settings_with(**overrides: Any) -> ApiSettings:
    """`ApiSettings` с обязательным секретом из окружения (см. tests/conftest.py).

    Через `**kwargs`, потому что pyright считает `jwt_secret` обязательным
    аргументом конструктора, хотя BaseSettings читает его из окружения.
    """
    return ApiSettings(**overrides)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in NAMES:
        monkeypatch.delenv(f"PULSE_API_{name}", raising=False)
    get_settings.cache_clear()


def test_defaults() -> None:
    settings = settings_with()

    assert settings.environment == "dev"
    assert settings.log_level == "INFO"
    assert settings.max_checks_per_monitor == 100


def test_values_are_read_from_prefixed_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PULSE_API_ENVIRONMENT", "prod")
    monkeypatch.setenv("PULSE_API_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("PULSE_API_MAX_CHECKS_PER_MONITOR", "25")

    settings = settings_with()

    assert (settings.environment, settings.log_level) == ("prod", "DEBUG")
    assert settings.max_checks_per_monitor == 25


def test_cli_prefix_is_not_read(monkeypatch: pytest.MonkeyPatch) -> None:
    # PULSE_ принадлежит CLI-шному Settings, PULSE_API_ только API.
    monkeypatch.setenv("PULSE_ENVIRONMENT", "prod")
    monkeypatch.setenv("PULSE_LOG_LEVEL", "DEBUG")

    settings = settings_with()

    assert (settings.environment, settings.log_level) == ("dev", "INFO")


@pytest.mark.parametrize("raw", ["info", "Info", "INFO", "warning"])
def test_log_level_is_case_insensitive(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    monkeypatch.setenv("PULSE_API_LOG_LEVEL", raw)

    assert settings_with().log_level == raw.upper()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("LOG_LEVEL", "VERBOSE"),
        ("LOG_LEVEL", ""),
        ("ENVIRONMENT", "staging"),
        ("ENVIRONMENT", "PROD"),
        ("MAX_CHECKS_PER_MONITOR", "0"),
        ("MAX_CHECKS_PER_MONITOR", "-5"),
        ("MAX_CHECKS_PER_MONITOR", "many"),
        ("MAX_CHECKS_PER_MONITOR", "1.5"),
    ],
)
def test_invalid_environment_value_is_rejected(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(f"PULSE_API_{name}", value)

    with pytest.raises(ValidationError) as exc_info:
        settings_with()

    assert [str(e["loc"][0]) for e in exc_info.value.errors()] == [name.lower()]


def test_settings_are_frozen() -> None:
    with pytest.raises(ValidationError):
        settings_with().environment = "prod"  # type: ignore[misc]


def test_host_and_port_are_not_settings() -> None:
    # Их принимает запускающий процесс, приложение их не читает.
    assert not {"host", "port"} & set(ApiSettings.model_fields)


def test_explicit_arguments_override_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PULSE_API_ENVIRONMENT", "prod")

    assert settings_with(environment="dev").environment == "dev"


# --- get_settings ----------------------------------------------------------


def test_get_settings_returns_the_same_object() -> None:
    assert get_settings() is get_settings()


def test_get_settings_does_not_see_environment_changes_until_cache_cleared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PULSE_API_ENVIRONMENT", "prod")
    assert get_settings().environment == "prod"

    monkeypatch.setenv("PULSE_API_ENVIRONMENT", "dev")
    assert get_settings().environment == "prod"

    get_settings.cache_clear()
    assert get_settings().environment == "dev"


def test_get_settings_raises_on_invalid_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PULSE_API_LOG_LEVEL", "VERBOSE")

    with pytest.raises(ValidationError):
        get_settings()


# --- JWT -------------------------------------------------------------------

LONG_SECRET = "s" * 32


def test_jwt_secret_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PULSE_API_JWT_SECRET")

    with pytest.raises(ValidationError) as exc_info:
        settings_with()

    errors = exc_info.value.errors()
    assert [(e["loc"], e["type"]) for e in errors] == [(("jwt_secret",), "missing")]


def test_jwt_secret_has_no_default() -> None:
    assert ApiSettings.model_fields["jwt_secret"].is_required()


@pytest.mark.parametrize("secret", ["", "short", "s" * 31])
def test_short_jwt_secret_is_rejected(
    monkeypatch: pytest.MonkeyPatch, secret: str
) -> None:
    monkeypatch.setenv("PULSE_API_JWT_SECRET", secret)

    with pytest.raises(ValidationError) as exc_info:
        settings_with()

    assert [str(e["loc"][0]) for e in exc_info.value.errors()] == ["jwt_secret"]


def test_jwt_secret_of_minimum_length_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PULSE_API_JWT_SECRET", LONG_SECRET)

    assert settings_with().jwt_secret.get_secret_value() == LONG_SECRET


def test_jwt_secret_is_hidden_in_repr_str_and_dump(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PULSE_API_JWT_SECRET", "distinctive-secret-value-0123456789")
    settings = settings_with()

    assert "distinctive-secret" not in repr(settings)
    assert "distinctive-secret" not in str(settings)
    assert "distinctive-secret" not in str(settings.model_dump())
    assert "distinctive-secret" not in settings.model_dump_json()


def test_validation_error_does_not_leak_the_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PULSE_API_JWT_SECRET", "too-short-secret")

    with pytest.raises(ValidationError) as exc_info:
        settings_with()

    assert "too-short-secret" not in str(exc_info.value)
    # .errors() отдаёт input по умолчанию, но печатается оно только в str(): его
    # и видит оператор в логе запуска.
    assert "too-short-secret" not in repr(exc_info.value.errors(include_input=False))


def test_access_token_lifetime_defaults_to_fifteen_minutes() -> None:
    assert settings_with().access_token_ttl_minutes == 15


def test_access_token_lifetime_is_read_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PULSE_API_ACCESS_TOKEN_TTL_MINUTES", "60")

    assert settings_with().access_token_ttl_minutes == 60


@pytest.mark.parametrize("value", ["0", "-5", "soon", "1.5"])
def test_invalid_access_token_lifetime_is_rejected(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("PULSE_API_ACCESS_TOKEN_TTL_MINUTES", value)

    with pytest.raises(ValidationError):
        settings_with()


# --- refresh и ограничение частоты -----------------------------------------


def test_new_settings_defaults() -> None:
    settings = settings_with()

    assert settings.refresh_token_ttl_days == 7
    assert settings.login_rate_limit_attempts == 5
    assert settings.login_rate_limit_window_seconds == 60


def test_new_settings_are_read_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PULSE_API_REFRESH_TOKEN_TTL_DAYS", "30")
    monkeypatch.setenv("PULSE_API_LOGIN_RATE_LIMIT_ATTEMPTS", "10")
    monkeypatch.setenv("PULSE_API_LOGIN_RATE_LIMIT_WINDOW_SECONDS", "300")

    settings = settings_with()

    assert settings.refresh_token_ttl_days == 30
    assert settings.login_rate_limit_attempts == 10
    assert settings.login_rate_limit_window_seconds == 300


@pytest.mark.parametrize(
    "name",
    [
        "REFRESH_TOKEN_TTL_DAYS",
        "LOGIN_RATE_LIMIT_ATTEMPTS",
        "LOGIN_RATE_LIMIT_WINDOW_SECONDS",
    ],
)
@pytest.mark.parametrize("value", ["0", "-1", "often", "1.5"])
def test_new_settings_reject_invalid_values(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(f"PULSE_API_{name}", value)

    with pytest.raises(ValidationError):
        settings_with()
