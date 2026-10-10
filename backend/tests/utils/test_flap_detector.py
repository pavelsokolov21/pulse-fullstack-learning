import pytest

from pulse.utils.flap_detector import FlapDetector, Status

OK, FAIL = True, False


def run(detector: FlapDetector, results: list[bool]) -> list[str]:
    return [detector.record(ok=ok) for ok in results]


def test_initial_status_is_up() -> None:
    assert FlapDetector(3, 2).status == "up"


@pytest.mark.parametrize(
    ("fail_threshold", "recover_threshold"),
    [(0, 2), (3, 0), (-1, 2), (3, -1)],
)
def test_invalid_thresholds(fail_threshold: int, recover_threshold: int) -> None:
    with pytest.raises(ValueError, match="threshold"):
        FlapDetector(fail_threshold, recover_threshold)


@pytest.mark.parametrize(
    ("fail_threshold", "recover_threshold"),
    [(1.5, 2), (3, 1.5), (True, 2), (3, False), (float("nan"), 2), (2.0, 2)],
)
def test_non_integer_thresholds(
    fail_threshold: object, recover_threshold: object
) -> None:
    # Дробный порог никогда не совпал бы со счётчиком, детектор не ушёл бы в DOWN.
    with pytest.raises(TypeError):
        FlapDetector(fail_threshold, recover_threshold)  # pyright: ignore[reportArgumentType]


@pytest.mark.parametrize(
    ("n", "m", "results", "expected"),
    [
        pytest.param(3, 2, [FAIL] * 3, ["up", "up", "down"], id="down-after-n-fails"),
        pytest.param(1, 1, [FAIL], ["down"], id="threshold-one"),
        pytest.param(
            3,
            2,
            [FAIL, FAIL, OK, FAIL, FAIL],
            ["up"] * 5,
            id="success-resets-fail-streak",
        ),
        pytest.param(3, 2, [OK] * 3, ["up"] * 3, id="only-successes"),
        pytest.param(
            3,
            2,
            [FAIL, FAIL, FAIL, FAIL, FAIL],
            ["up", "up", "down", "down", "down"],
            id="stays-down-on-more-fails",
        ),
        pytest.param(
            3,
            2,
            [FAIL, FAIL, FAIL, OK, OK],
            ["up", "up", "down", "down", "up"],
            id="recovers-after-m-successes",
        ),
        pytest.param(
            3,
            2,
            [FAIL, FAIL, FAIL, OK, FAIL, OK],
            ["up", "up", "down", "down", "down", "down"],
            id="fail-resets-recovery-streak",
        ),
        pytest.param(
            2,
            1,
            [FAIL, FAIL, OK, FAIL, FAIL],
            ["up", "down", "up", "up", "down"],
            id="counters-reset-after-recovery",
        ),
    ],
)
def test_status_sequence(
    n: int, m: int, results: list[bool], expected: list[str]
) -> None:
    assert run(FlapDetector(n, m), results) == expected


def test_record_returns_new_status_after_transition() -> None:
    detector = FlapDetector(1, 1)

    assert detector.record(ok=False) is Status.DOWN
    assert detector.status is Status.DOWN


def test_default_thresholds() -> None:
    detector = FlapDetector()

    assert run(detector, [FAIL] * 3 + [OK] * 2) == [
        "up",
        "up",
        "down",
        "down",
        "up",
    ]


def test_instances_do_not_share_state() -> None:
    a, b = FlapDetector(1, 1), FlapDetector(1, 1)
    a.record(ok=False)
    assert a.status == "down"
    assert b.status == "up"
