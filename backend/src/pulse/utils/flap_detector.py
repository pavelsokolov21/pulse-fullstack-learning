from enum import StrEnum

from pulse.decorators.validation import validate_range


class Status(StrEnum):
    UP = "up"
    DOWN = "down"


class FlapDetector:
    @validate_range(argument_name="fail_threshold", range=(1, float("inf")))
    @validate_range(argument_name="recover_threshold", range=(1, float("inf")))
    def __init__(self, fail_threshold: int = 3, recover_threshold: int = 2):
        self._fail_threshold_max = fail_threshold
        self._recover_threshold_max = recover_threshold
        self._fail_threshold = 0
        self._recover_threshold = 0
        self._state = Status.UP

    @property
    def status(self):
        return self._state

    def record(self, ok: bool):
        if self._state == Status.UP:
            self._recover_threshold = 0

            if ok:
                self._fail_threshold = 0
            else:
                self._fail_threshold += 1

        if self._state == Status.DOWN:
            self._fail_threshold = 0

            if ok:
                self._recover_threshold += 1
            else:
                self._recover_threshold = 0

        if self._fail_threshold == self._fail_threshold_max:
            self._state = Status.DOWN

        if self._recover_threshold == self._recover_threshold_max:
            self._state = Status.UP

        return self._state
