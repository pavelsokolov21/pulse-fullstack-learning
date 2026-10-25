import pytest
from pydantic import ValidationError

from pulse.settings import Settings

ENV_NAMES = ("CONCURRENCY", "RETRIES", "RETRY_DELAY")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Тесты не должны зависеть от переменных, которые есть в оболочке разработчика.
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(f"PULSE_{name}", raising=False)


def error_types(
    exc_info: pytest.ExceptionInfo[ValidationError],
) -> list[tuple[str, str]]:
    return [(str(e["loc"][0]), e["type"]) for e in exc_info.value.errors()]


# --- значения по умолчанию -------------------------------------------------


def test_defaults_without_environment() -> None:
    settings = Settings()

    assert settings.concurrency == 10
    assert settings.retries == 2
    assert settings.retry_delay == 0.5


# --- чтение из окружения ---------------------------------------------------


def test_values_are_read_from_prefixed_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PULSE_CONCURRENCY", "50")
    monkeypatch.setenv("PULSE_RETRIES", "5")
    monkeypatch.setenv("PULSE_RETRY_DELAY", "1.5")

    settings = Settings()

    assert settings.concurrency == 50
    assert settings.retries == 5
    assert settings.retry_delay == 1.5


def test_environment_strings_are_converted_to_declared_types(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Переменные окружения всегда строки: тип приводит pydantic-settings.
    monkeypatch.setenv("PULSE_CONCURRENCY", "7")
    monkeypatch.setenv("PULSE_RETRY_DELAY", "2")

    settings = Settings()

    assert isinstance(settings.concurrency, int)
    assert isinstance(settings.retry_delay, float)
    assert settings.retry_delay == 2.0


def test_only_set_variables_are_overridden(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PULSE_RETRIES", "0")

    settings = Settings()

    assert settings.retries == 0
    assert settings.concurrency == 10
    assert settings.retry_delay == 0.5


def test_variable_names_are_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("pulse_concurrency", "3")

    assert Settings().concurrency == 3


def test_variables_without_prefix_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    # Общее имя вроде RETRIES может принадлежать другой программе в окружении.
    monkeypatch.setenv("CONCURRENCY", "99")
    monkeypatch.setenv("RETRIES", "99")

    settings = Settings()

    assert settings.concurrency == 10
    assert settings.retries == 2


def test_unknown_prefixed_variable_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    # Для окружения extra="forbid" не нужен: там живёт много чужих переменных.
    monkeypatch.setenv("PULSE_UNKNOWN", "1")

    assert Settings().concurrency == 10


def test_init_arguments_win_over_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PULSE_CONCURRENCY", "50")

    assert Settings(concurrency=3).concurrency == 3


# --- границы ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "value"),
    [("concurrency", 1), ("retries", 0), ("retry_delay", 0)],
)
def test_lower_bounds_are_inclusive(
    monkeypatch: pytest.MonkeyPatch, name: str, value: int
) -> None:
    # retries=0 (без повторов) и retry_delay=0 (без паузы) осмысленные настройки.
    monkeypatch.setenv(f"PULSE_{name.upper()}", str(value))

    assert getattr(Settings(), name) == value


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("concurrency", "0"),
        ("concurrency", "-1"),
        ("retries", "-1"),
        ("retry_delay", "-0.1"),
        ("retry_delay", "nan"),
    ],
)
def test_out_of_range_environment_value_is_rejected(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(f"PULSE_{name.upper()}", value)

    with pytest.raises(ValidationError) as exc_info:
        Settings()

    assert [field for field, _ in error_types(exc_info)] == [name]


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("concurrency", "many"),
        ("concurrency", "1.5"),
        ("concurrency", ""),
        ("retries", "two"),
        ("retries", "1.5"),
        ("retry_delay", "soon"),
    ],
)
def test_non_numeric_environment_value_is_rejected(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(f"PULSE_{name.upper()}", value)

    with pytest.raises(ValidationError) as exc_info:
        Settings()

    assert [field for field, _ in error_types(exc_info)] == [name]


def test_all_invalid_fields_are_reported_together(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PULSE_CONCURRENCY", "0")
    monkeypatch.setenv("PULSE_RETRIES", "-1")

    with pytest.raises(ValidationError) as exc_info:
        Settings()

    assert sorted(field for field, _ in error_types(exc_info)) == [
        "concurrency",
        "retries",
    ]


def test_invalid_init_argument_is_rejected() -> None:
    with pytest.raises(ValidationError) as exc_info:
        Settings(concurrency=0)

    assert error_types(exc_info) == [("concurrency", "greater_than_equal")]


# --- неизменяемость и описания ---------------------------------------------


def test_settings_are_frozen() -> None:
    settings = Settings()

    with pytest.raises(ValidationError) as exc_info:
        settings.concurrency = 5

    assert exc_info.value.errors()[0]["type"] == "frozen_instance"


def test_field_descriptions_match_their_fields() -> None:
    # Описание попадёт в --help и документацию: оно не должно уехать на чужое поле.
    fields = Settings.model_fields

    assert fields["retries"].description == "Число повторов после неудачи"
    assert fields["retry_delay"].description == "Пауза между повторами, секунды"
