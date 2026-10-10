from collections.abc import Sequence

import pytest

from pulse.utils.percentile import percentile


@pytest.mark.parametrize(("seq", "p"), [((), 10), ([], 10)])
def test_empty_list(seq: Sequence[float], p: float) -> None:
    with pytest.raises(ValueError, match="пуст"):
        percentile(seq, p)


@pytest.mark.parametrize(("seq", "p"), [((1, 2, 3), -1), ((1, 2, 3), 101)])
def test_p_invalid_range(seq: Sequence[float], p: float) -> None:
    with pytest.raises(ValueError, match="'p'"):
        percentile(seq, p)


def test_zero_p() -> None:
    res = percentile((1, 2, 3), 0)

    assert res == 1


@pytest.mark.parametrize("p", [True, False])
def test_p_bool_is_rejected(p: float) -> None:
    # bool это подкласс int: без явной проверки True молча стал бы p=1.
    with pytest.raises(TypeError):
        percentile((1, 2, 3), p)


def test_p_nan_is_rejected() -> None:
    with pytest.raises(ValueError, match="NaN"):
        percentile((1, 2, 3), float("nan"))


def test_nan_in_data_is_rejected() -> None:
    # sorted() с nan не определён: порядок зависит от позиции nan.
    with pytest.raises(ValueError, match="NaN"):
        percentile((3.0, float("nan"), 1.0, 2.0), 50)


@pytest.mark.parametrize(
    ("p", "expected"),
    [
        # 16.1 * 1000 / 100 в float даёт 161.00000000000003, ceil даёт 162.
        (16.1, 161),
        (32.2, 322),
        (64.4, 644),
        (99.9, 999),
        (0.1, 1),
    ],
)
def test_float_p_does_not_suffer_from_binary_rounding(p: float, expected: int) -> None:
    assert percentile(tuple(range(1, 1001)), p) == expected


@pytest.mark.parametrize(
    ("seq", "p", "expected"),
    [
        ((42,), 0, 42),
        ((42,), 50, 42),
        ((42,), 100, 42),
        ((10, 20, 30, 40, 50), 20, 10),
        ((10, 20, 30, 40, 50), 50, 30),
        ((10, 20, 30, 40, 50), 95, 50),
        ((10, 20, 30, 40), 50, 20),
        ((10, 20, 30, 40), 75, 30),
        ((10, 20, 30), 0, 10),
        ((10, 20, 30), 100, 30),
        ((50, 10, 40, 20, 30), 50, 30),
        ((10, 10, 10, 20), 75, 10),
        ((10, 10, 10, 20), 100, 20),
        ((5, 5, 5), 95, 5),
        (tuple(range(1, 101)), 7, 7),
    ],
)
def test_percentile_valid(seq: Sequence[float], p: float, expected: float) -> None:
    assert percentile(seq, p) == expected
