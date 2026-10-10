import math
from collections.abc import Sequence
from fractions import Fraction

from pulse.decorators.validation import validate_not_empty, validate_range


@validate_not_empty(
    argument_name="seq",
    message="Список пуст",
)
@validate_range(argument_name="p", min_value=0, max_value=100)
def percentile(seq: Sequence[float], p: float) -> float:
    """Перцентиль методом nearest-rank: всегда возвращает элемент из `seq`.

    `numpy.percentile` по умолчанию интерполирует между соседями, значения
    могут отличаться.
    """
    if any(math.isnan(x) for x in seq):
        raise ValueError("В данных есть NaN: порядок сортировки не определён")

    sorted_seq = sorted(seq)

    # Fraction(str(p)) берёт десятичную запись («16.1»), а не двоичное
    # приближение float, поэтому p * n / 100 считается точно. На float
    # 16.1 * 1000 / 100 == 161.00000000000003 и ceil давал бы 162.
    rank = math.ceil(Fraction(str(p)) * len(sorted_seq) / 100)

    return sorted_seq[max(rank, 1) - 1]
