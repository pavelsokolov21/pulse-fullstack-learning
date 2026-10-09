from datetime import timedelta

import pytest

from pulse.utils.parse_interval import parse_interval


@pytest.mark.parametrize("input,expected", [("30s", 30), ("30S", 30)])
def test_seconds(input: str, expected: int):
    res = parse_interval(input)
    expected_res = timedelta(seconds=expected)

    assert expected_res == res


@pytest.mark.parametrize("input,expected", [("5m", 5), ("5M", 5)])
def test_minutes(input: str, expected: int):
    res = parse_interval(input)
    expected_res = timedelta(minutes=expected)

    assert expected_res == res


@pytest.mark.parametrize("input,expected", [("1h", 1), ("1H", 1)])
def test_hours(input: str, expected: int):
    res = parse_interval(input)
    expected_res = timedelta(hours=expected)

    assert expected_res == res


@pytest.mark.parametrize(
    "invalid_input",
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
    ],
)
def test_parse_interval_raises_value_error(invalid_input: str):
    with pytest.raises(ValueError):
        parse_interval(invalid_input)
