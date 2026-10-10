from datetime import timedelta

import pytest

from pulse.utils.parse_interval import parse_interval


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("30s", timedelta(seconds=30)),
        ("30S", timedelta(seconds=30)),
        ("5m", timedelta(minutes=5)),
        ("5M", timedelta(minutes=5)),
        ("1h", timedelta(hours=1)),
        ("1H", timedelta(hours=1)),
        ("90m", timedelta(minutes=90)),
        ("007s", timedelta(seconds=7)),
        (" 5m ", timedelta(minutes=5)),
        ("\t5m\n", timedelta(minutes=5)),
    ],
)
def test_parse_interval_valid(raw: str, expected: timedelta) -> None:
    assert parse_interval(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "abc",
        "12г",
        "-5s",
        "10 s",
        "1.5h",
        "1h 30m",
        "1h30m",
        "10j",
        "m",
        "5",
        "5ms",
        # \d в Python 3 матчит любые Unicode-цифры, нужны только ASCII.
        "٥m",
        "５m",
    ],
)
def test_parse_interval_rejects_bad_format(raw: str) -> None:
    with pytest.raises(ValueError, match="Неверный формат"):
        parse_interval(raw)


@pytest.mark.parametrize("raw", ["0s", "0m", "0h", "000s"])
def test_parse_interval_rejects_zero(raw: str) -> None:
    # Нулевой интервал превратил бы проверки в busy-loop.
    with pytest.raises(ValueError, match="больше нуля"):
        parse_interval(raw)


def test_parse_interval_rejects_overflow_as_value_error() -> None:
    # timedelta бросает OverflowError, наружу должен выйти ValueError.
    with pytest.raises(ValueError, match="Слишком большой"):
        parse_interval("99999999999h")


def test_parse_interval_error_mentions_original_input() -> None:
    with pytest.raises(ValueError, match="0s"):
        parse_interval("0s")
