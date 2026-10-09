from collections.abc import Sequence

import pytest

from pulse.utils.percentile import percentile


@pytest.mark.parametrize("seq,p", [((), 10), ([], 10)])
def test_empty_list(seq: Sequence[float], p: float):
    with pytest.raises(ValueError):
        percentile(seq, p)


@pytest.mark.parametrize("seq,p", [((1, 2, 3), -1), ((1, 2, 3), 101)])
def test_p_invalid_range(seq: Sequence[float], p: float):
    with pytest.raises(ValueError):
        percentile(seq, p)


def test_zero_p():
    res = percentile((1, 2, 3), 0)

    assert res == 1


@pytest.mark.parametrize(
    "seq,p,expected",
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
