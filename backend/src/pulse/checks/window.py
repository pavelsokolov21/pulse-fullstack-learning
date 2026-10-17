from collections import deque

from pulse.checks.models import CheckResult
from pulse.decorators.validation import validate_range
from pulse.utils.stats import get_failure_rate, get_p95


class RollingWindow:
    """Последние `size` проверок: по ним видно дребезг и деградацию монитора.

    При переполнении самая старая проверка молча вытесняется. Показатели
    считаются по разным выборкам: доля сбоев по всем проверкам окна, `p95`
    только по проверкам с ответом (задержка сетевого сбоя это таймаут, а не
    скорость сайта). Для пустого окна оба равны `None`: данных нет, а не «0».
    """

    @validate_range(argument_name="size", min_value=1, integer=True)
    def __init__(self, size: int) -> None:
        self._results: deque[CheckResult] = deque(maxlen=size)

    def add(self, result: CheckResult) -> None:
        self._results.append(result)

    def __len__(self) -> int:
        return len(self._results)

    def failure_rate(self) -> float | None:
        return get_failure_rate(self._results)

    def p95(self) -> float | None:
        return get_p95(self._results)
