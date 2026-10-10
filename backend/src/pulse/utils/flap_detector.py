from enum import StrEnum

from pulse.decorators.validation import validate_range


class Status(StrEnum):
    UP = "up"
    DOWN = "down"


class FlapDetector:
    """Переключает статус только после серии одинаковых результатов.

    `fail_threshold` неудач подряд переводят UP в DOWN, `recover_threshold`
    успехов подряд возвращают DOWN в UP. Одиночный сбой статус не меняет.
    """

    @validate_range(argument_name="fail_threshold", min_value=1, integer=True)
    @validate_range(argument_name="recover_threshold", min_value=1, integer=True)
    def __init__(self, fail_threshold: int = 3, recover_threshold: int = 2) -> None:
        self._fail_threshold = fail_threshold
        self._recover_threshold = recover_threshold
        self._fail_streak = 0
        self._recover_streak = 0
        self._state = Status.UP

    @property
    def status(self) -> Status:
        return self._state

    def record(self, ok: bool) -> Status:
        if self._state == Status.UP:
            self._recover_streak = 0

            if ok:
                self._fail_streak = 0
            else:
                self._fail_streak += 1

        if self._state == Status.DOWN:
            self._fail_streak = 0

            if ok:
                self._recover_streak += 1
            else:
                self._recover_streak = 0

        if self._fail_streak == self._fail_threshold:
            self._state = Status.DOWN

        if self._recover_streak == self._recover_threshold:
            self._state = Status.UP

        return self._state
