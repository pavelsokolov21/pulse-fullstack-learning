import logging

from pulse.core.request_context import (
    RequestIdFilter,
    get_request_id,
    new_request_id,
    request_id_var,
)


def test_request_id_is_none_outside_a_request() -> None:
    assert get_request_id() is None


def test_get_request_id_returns_value_set_in_context() -> None:
    token = request_id_var.set("abc")
    try:
        assert get_request_id() == "abc"
    finally:
        request_id_var.reset(token)


def test_new_request_ids_are_unique_32_char_hex() -> None:
    ids = {new_request_id() for _ in range(50)}

    assert len(ids) == 50
    assert all(len(i) == 32 and int(i, 16) >= 0 for i in ids)


# --- фильтр логов ----------------------------------------------------------


def make_record() -> logging.LogRecord:
    return logging.LogRecord("t", logging.INFO, __file__, 1, "msg", None, None)


def test_filter_adds_current_request_id() -> None:
    token = request_id_var.set("abc")
    try:
        record = make_record()
        assert RequestIdFilter().filter(record) is True
    finally:
        request_id_var.reset(token)

    assert record.__dict__["request_id"] == "abc"


def test_filter_uses_dash_outside_a_request() -> None:
    record = make_record()

    RequestIdFilter().filter(record)

    assert record.__dict__["request_id"] == "-"


def test_filter_keeps_explicit_request_id_from_extra() -> None:
    token = request_id_var.set("from-context")
    try:
        record = make_record()
        record.__dict__["request_id"] = "from-extra"
        RequestIdFilter().filter(record)
    finally:
        request_id_var.reset(token)

    assert record.__dict__["request_id"] == "from-extra"
