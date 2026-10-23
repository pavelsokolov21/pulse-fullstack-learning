import tomllib
from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from pulse.monitors.loader import load_monitors
from pulse.monitors.models import MonitorConfig

VALID_TOML = """
[[monitors]]
url = "example.com"
interval = "5m"
timeout = 10

[[monitors]]
url = "https://api.example.com/health"
interval = "30s"
"""


def write(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "monitors.toml"
    path.write_text(content, encoding="utf-8")
    return path


# --- успешная загрузка -----------------------------------------------------


def test_loads_monitors_in_file_order(tmp_path: Path) -> None:
    monitors = load_monitors(write(tmp_path, VALID_TOML))

    assert [m.url for m in monitors] == [
        "https://example.com",
        "https://api.example.com/health",
    ]


def test_loaded_values_are_validated_and_converted(tmp_path: Path) -> None:
    first, second = load_monitors(write(tmp_path, VALID_TOML))

    assert first == MonitorConfig.model_validate(
        {"url": "example.com", "interval": "5m", "timeout": 10}
    )
    assert first.interval == timedelta(minutes=5)
    assert first.timeout == 10.0
    # У второго timeout не задан: действует значение по умолчанию.
    assert second.interval == timedelta(seconds=30)
    assert second.timeout == 10.0


def test_single_monitor_is_enough(tmp_path: Path) -> None:
    content = '[[monitors]]\nurl = "a.test"\ninterval = "1h"\n'

    assert len(load_monitors(write(tmp_path, content))) == 1


def test_file_with_cyrillic_comments_is_read_as_utf8(tmp_path: Path) -> None:
    content = "# Мониторы проекта\n" + VALID_TOML

    assert len(load_monitors(write(tmp_path, content))) == 2


def test_other_top_level_keys_are_ignored(tmp_path: Path) -> None:
    content = 'title = "prod"\n' + VALID_TOML

    assert len(load_monitors(write(tmp_path, content))) == 2


# --- файл без мониторов ----------------------------------------------------


@pytest.mark.parametrize(
    "content",
    ['title = "empty"\n', "", "monitors = []\n"],
    ids=["no-key", "empty-file", "empty-list"],
)
def test_file_without_monitors_is_an_error(tmp_path: Path, content: str) -> None:
    # Пустой результат скрыл бы ошибку: с таким конфигом проверять нечего.
    with pytest.raises(ValueError, match="нет ни одной секции"):
        load_monitors(write(tmp_path, content))


@pytest.mark.parametrize(
    "content",
    ['monitors = "oops"\n', "monitors = 5\n", '[monitors]\nurl = "a.test"\n'],
    ids=["string", "number", "single-table"],
)
def test_monitors_must_be_an_array_of_tables(tmp_path: Path, content: str) -> None:
    with pytest.raises(ValueError, match="массивом таблиц"):
        load_monitors(write(tmp_path, content))


def test_error_message_contains_file_path(tmp_path: Path) -> None:
    path = write(tmp_path, 'title = "empty"\n')

    with pytest.raises(ValueError, match=r"monitors\.toml"):
        load_monitors(path)


# --- ошибки файла и содержимого --------------------------------------------


def test_missing_file_raises_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_monitors(tmp_path / "nope.toml")


def test_broken_toml_raises_decode_error(tmp_path: Path) -> None:
    with pytest.raises(tomllib.TOMLDecodeError):
        load_monitors(write(tmp_path, "[[monitors\nurl = 1\n"))


@pytest.mark.parametrize(
    ("content", "field", "error_type"),
    [
        ('[[monitors]]\nurl = "a.test"\ninterval = "0s"\n', "interval", "value_error"),
        ('[[monitors]]\ninterval = "5m"\n', "url", "missing"),
        ('[[monitors]]\nurl = "a.test"\n', "interval", "missing"),
        (
            '[[monitors]]\nurl = "a.test"\ninterval = "5m"\ntimeout = 0\n',
            "timeout",
            "greater_than",
        ),
        (
            '[[monitors]]\nurl = "a.test"\ninterval = "5m"\nretries = 3\n',
            "retries",
            "extra_forbidden",
        ),
    ],
    ids=["bad-interval", "no-url", "no-interval", "bad-timeout", "unknown-field"],
)
def test_invalid_monitor_raises_validation_error(
    tmp_path: Path, content: str, field: str, error_type: str
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        load_monitors(write(tmp_path, content))

    assert [(str(e["loc"][0]), e["type"]) for e in exc_info.value.errors()] == [
        (field, error_type)
    ]


def test_one_invalid_monitor_fails_the_whole_file(tmp_path: Path) -> None:
    # Все или ничего: частично загруженный конфиг хуже явной ошибки.
    content = VALID_TOML + '\n[[monitors]]\nurl = ""\ninterval = "5m"\n'

    with pytest.raises(ValidationError):
        load_monitors(write(tmp_path, content))
