import pytest
from pydantic import ValidationError

from pulse.api.settings import ApiSettings, get_settings

NAMES = ("ENVIRONMENT", "LOG_LEVEL", "MAX_CHECKS_PER_MONITOR")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in NAMES:
        monkeypatch.delenv(f"PULSE_API_{name}", raising=False)
    get_settings.cache_clear()


def test_defaults() -> None:
    settings = ApiSettings()

    assert settings.environment == "dev"
    assert settings.log_level == "INFO"
    assert settings.max_checks_per_monitor == 100


def test_values_are_read_from_prefixed_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PULSE_API_ENVIRONMENT", "prod")
    monkeypatch.setenv("PULSE_API_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("PULSE_API_MAX_CHECKS_PER_MONITOR", "25")

    settings = ApiSettings()

    assert (settings.environment, settings.log_level) == ("prod", "DEBUG")
    assert settings.max_checks_per_monitor == 25


def test_cli_prefix_is_not_read(monkeypatch: pytest.MonkeyPatch) -> None:
    # PULSE_ принадлежит CLI-шному Settings, PULSE_API_ только API.
    monkeypatch.setenv("PULSE_ENVIRONMENT", "prod")
    monkeypatch.setenv("PULSE_LOG_LEVEL", "DEBUG")

    settings = ApiSettings()

    assert (settings.environment, settings.log_level) == ("dev", "INFO")


@pytest.mark.parametrize("raw", ["info", "Info", "INFO", "warning"])
def test_log_level_is_case_insensitive(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    monkeypatch.setenv("PULSE_API_LOG_LEVEL", raw)

    assert ApiSettings().log_level == raw.upper()


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
        ApiSettings()

    assert [str(e["loc"][0]) for e in exc_info.value.errors()] == [name.lower()]


def test_settings_are_frozen() -> None:
    with pytest.raises(ValidationError):
        ApiSettings().environment = "prod"  # type: ignore[misc]


def test_host_and_port_are_not_settings() -> None:
    # Их принимает запускающий процесс, приложение их не читает.
    assert not {"host", "port"} & set(ApiSettings.model_fields)


def test_explicit_arguments_override_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PULSE_API_ENVIRONMENT", "prod")

    assert ApiSettings(environment="dev").environment == "dev"


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
