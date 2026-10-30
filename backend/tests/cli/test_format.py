import json

import pytest

from pulse.checks.models import CheckResult
from pulse.cli.main import format_json, format_table

OK = CheckResult("https://a.test", 200, 12.4, None)
SERVER_ERROR = CheckResult("https://c.test", 503, 80.0, None)
NETWORK_ERROR = CheckResult("https://bb.test/длинный", None, 3000.0, "Network Error: x")


# --- format_table ----------------------------------------------------------


def test_table_has_header_and_a_row_per_result() -> None:
    lines = format_table([OK, SERVER_ERROR, NETWORK_ERROR]).splitlines()

    assert len(lines) == 4
    assert lines[0].split() == ["STATUS", "CODE", "MS", "URL", "ERROR"]


def test_table_rows_keep_input_order() -> None:
    lines = format_table([SERVER_ERROR, OK]).splitlines()[1:]

    assert [line.split()[3] for line in lines] == ["https://c.test", "https://a.test"]


@pytest.mark.parametrize(
    ("result", "status", "code"),
    [
        (OK, "OK", "200"),
        (CheckResult("u.test", 301, 1.0, None), "OK", "301"),
        (CheckResult("u.test", 400, 1.0, None), "FAIL", "400"),
        (SERVER_ERROR, "FAIL", "503"),
        (NETWORK_ERROR, "FAIL", "-"),
    ],
)
def test_table_row_shows_status_and_code(
    result: CheckResult, status: str, code: str
) -> None:
    row = format_table([result]).splitlines()[1].split()

    assert row[:2] == [status, code]


@pytest.mark.parametrize(
    ("latency", "expected"), [(12.4, "12"), (12.6, "13"), (3000.0, "3000"), (0.0, "0")]
)
def test_table_latency_is_rounded_to_whole_milliseconds(
    latency: float, expected: str
) -> None:
    result = CheckResult("u.test", 200, latency, None)

    assert format_table([result]).splitlines()[1].split()[2] == expected


def test_table_shows_error_text_in_last_column() -> None:
    row = format_table([NETWORK_ERROR]).splitlines()[1]

    assert row.endswith("Network Error: x")


def test_table_columns_are_aligned() -> None:
    lines = format_table([OK, NETWORK_ERROR]).splitlines()
    url_start = lines[0].index("URL")
    error_start = lines[0].index("ERROR")

    assert lines[1][url_start:].startswith("https://a.test")
    assert lines[2][url_start:].startswith("https://bb.test/длинный")
    assert lines[2][error_start:] == "Network Error: x"


def test_table_has_no_trailing_spaces() -> None:
    lines = format_table([OK, NETWORK_ERROR]).splitlines()

    assert all(line == line.rstrip() for line in lines)


def test_table_without_results_is_only_the_header() -> None:
    assert format_table([]).split() == ["STATUS", "CODE", "MS", "URL", "ERROR"]


def test_table_accepts_any_sequence() -> None:
    assert format_table((OK,)) == format_table([OK])


# --- format_json -----------------------------------------------------------


def test_json_contains_every_field_and_ok_flag() -> None:
    [item] = json.loads(format_json([NETWORK_ERROR]))

    assert item == {
        "url": "https://bb.test/длинный",
        "status_code": None,
        "latency_ms": 3000.0,
        "error": "Network Error: x",
        "ok": False,
    }


@pytest.mark.parametrize(
    ("result", "ok"), [(OK, True), (SERVER_ERROR, False), (NETWORK_ERROR, False)]
)
def test_json_ok_matches_result(result: CheckResult, ok: bool) -> None:
    assert json.loads(format_json([result]))[0]["ok"] is ok


def test_json_keeps_input_order() -> None:
    items = json.loads(format_json([SERVER_ERROR, OK]))

    assert [i["url"] for i in items] == ["https://c.test", "https://a.test"]


def test_json_without_results_is_empty_array() -> None:
    assert json.loads(format_json([])) == []


def test_json_keeps_non_ascii_readable() -> None:
    # Без ensure_ascii=False кириллица в URL превратилась бы в \uXXXX.
    assert "длинный" in format_json([NETWORK_ERROR])
