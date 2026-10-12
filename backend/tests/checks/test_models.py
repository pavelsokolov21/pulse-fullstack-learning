import dataclasses

import pytest

from pulse.checks.models import CheckResult


def make_result(status_code: int | None) -> CheckResult:
    return CheckResult(
        url="https://example.com",
        status_code=status_code,
        latency_ms=0.0,
        error=None,
    )


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        (None, False),
        (100, False),
        (199, False),
        (200, True),
        (204, True),
        (399, True),
        (400, False),
        (503, False),
    ],
)
def test_ok_depends_on_status_code(status_code: int | None, expected: bool) -> None:
    assert make_result(status_code).ok is expected


def test_check_result_is_immutable() -> None:
    result = make_result(200)

    with pytest.raises(dataclasses.FrozenInstanceError):
        # Намеренно пишем в frozen-поле, pyright это тоже видит.
        result.status_code = 500  # pyright: ignore[reportAttributeAccessIssue]
