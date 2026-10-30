from datetime import timedelta

import pytest
from pydantic import ValidationError

from pulse.domain.monitors import MonitorConfig


def make(**overrides: object) -> MonitorConfig:
    data: dict[str, object] = {"url": "example.com", "interval": "5m", **overrides}
    return MonitorConfig.model_validate(data)


def error_types(
    exc_info: pytest.ExceptionInfo[ValidationError],
) -> list[tuple[str, str]]:
    return [(str(e["loc"][0]), e["type"]) for e in exc_info.value.errors()]


# --- url -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("example.com", "https://example.com"),
        ("Example.COM/Path", "https://example.com/Path"),
        ("http://example.com:80/", "http://example.com"),
        ("  api.example.com/health  ", "https://api.example.com/health"),
    ],
)
def test_url_is_normalized(raw: str, expected: str) -> None:
    assert make(url=raw).url == expected


@pytest.mark.parametrize(
    "url",
    ["", "   ", "ftp://example.com", "http://user:pass@example.com", "exa mple.com"],
)
def test_invalid_url_is_rejected(url: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        make(url=url)

    assert error_types(exc_info) == [("url", "value_error")]


def test_non_string_url_is_rejected() -> None:
    with pytest.raises(ValidationError) as exc_info:
        make(url=5)

    assert error_types(exc_info) == [("url", "string_type")]


# --- interval --------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("30s", timedelta(seconds=30)),
        ("5m", timedelta(minutes=5)),
        ("1h", timedelta(hours=1)),
        (" 5M ", timedelta(minutes=5)),
    ],
)
def test_interval_string_is_parsed(raw: str, expected: timedelta) -> None:
    assert make(interval=raw).interval == expected


def test_interval_accepts_ready_timedelta() -> None:
    assert make(interval=timedelta(minutes=2)).interval == timedelta(minutes=2)


@pytest.mark.parametrize("interval", [timedelta(0), timedelta(seconds=-1)])
def test_non_positive_timedelta_is_rejected(interval: timedelta) -> None:
    with pytest.raises(ValidationError) as exc_info:
        make(interval=interval)

    assert error_types(exc_info) == [("interval", "value_error")]


def test_numeric_interval_is_read_as_seconds() -> None:
    # Это lax-режим Pydantic, а не наше решение: `interval = 300` в TOML молча
    # станет 300 секундами, хотя строки требуют единицу ("5m"). Если такое
    # поведение не нужно, его запрещают отдельно (strict или проверка типа).
    assert make(interval=300).interval == timedelta(seconds=300)


@pytest.mark.parametrize(
    "interval",
    [
        "0s",
        "abc",
        "1.5h",
        "PT5M",
        "5",
        "",
        0,
        -5,
        -0.5,
        True,
        False,
    ],
)
def test_invalid_interval_is_rejected(interval: object) -> None:
    with pytest.raises(ValidationError) as exc_info:
        make(interval=interval)

    assert error_types(exc_info) == [("interval", "value_error")]


@pytest.mark.parametrize("interval", [float("nan"), float("inf"), 1e30])
def test_non_finite_or_huge_numeric_interval_is_rejected(interval: float) -> None:
    with pytest.raises(ValidationError) as exc_info:
        make(interval=interval)

    assert error_types(exc_info) == [("interval", "time_delta_parsing")]


# --- timeout ---------------------------------------------------------------


def test_timeout_defaults_to_ten_seconds() -> None:
    assert make().timeout == 10.0


def test_integer_timeout_becomes_float() -> None:
    timeout = make(timeout=5).timeout

    assert timeout == 5.0
    assert isinstance(timeout, float)


@pytest.mark.parametrize("timeout", [0, -1, -0.5])
def test_non_positive_timeout_is_rejected(timeout: float) -> None:
    with pytest.raises(ValidationError) as exc_info:
        make(timeout=timeout)

    assert error_types(exc_info) == [("timeout", "greater_than")]


@pytest.mark.parametrize("timeout", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_timeout_is_rejected(timeout: float) -> None:
    # JSON не умеет inf и NaN: такой монитор нельзя было бы вернуть клиенту.
    with pytest.raises(ValidationError) as exc_info:
        make(timeout=timeout)

    assert error_types(exc_info) == [("timeout", "finite_number")]


# --- структура модели ------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "missing"),
    [({"interval": "5m"}, "url"), ({"url": "example.com"}, "interval")],
)
def test_required_fields_are_enforced(data: dict[str, object], missing: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        MonitorConfig.model_validate(data)

    assert error_types(exc_info) == [(missing, "missing")]


def test_unknown_field_is_rejected() -> None:
    # extra="forbid": опечатка в ключе файла не должна молча игнорироваться.
    with pytest.raises(ValidationError) as exc_info:
        make(retries=3)

    assert error_types(exc_info) == [("retries", "extra_forbidden")]


def test_config_is_frozen() -> None:
    config = make()

    with pytest.raises(ValidationError) as exc_info:
        config.timeout = 5.0

    assert exc_info.value.errors()[0]["type"] == "frozen_instance"


def test_dump_and_validate_round_trip() -> None:
    config = make(url="Example.com/a", interval="30s", timeout=3)

    assert MonitorConfig.model_validate(config.model_dump()) == config


def test_validation_error_is_a_value_error() -> None:
    # Поэтому `except ValueError` в вызывающем коде ловит и ошибки конфига.
    assert issubclass(ValidationError, ValueError)
