from collections.abc import Sequence
from math import ceil

from pulse.decorators.validation import validate_not_empty, validate_range


@validate_not_empty(
    argument_name="seq",
    message="Список пуст",
)
@validate_range(argument_name="p", range=(0, 100))
def percentile(seq: Sequence[float], p: float):
    sorted_seq = sorted(seq)
    n = len(seq)

    if p == 0:
        return sorted_seq[0]

    rank = ceil(p * n / 100)

    return sorted_seq[rank - 1]
