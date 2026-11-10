import pytest

from pulse.domain.errors import ConcurrentUpdateError
from pulse.services.retry import retry_on_conflict


class Flaky:
    """Операция, которая `failures` раз подряд упирается в конфликт."""

    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls = 0

    async def __call__(self) -> str:
        self.calls += 1
        if self.calls <= self.failures:
            raise ConcurrentUpdateError(1)
        return "ok"


async def test_returns_the_result_without_retrying_on_success() -> None:
    operation = Flaky(failures=0)

    assert await retry_on_conflict(operation, base_delay=0) == "ok"
    assert operation.calls == 1


async def test_retries_after_conflicts_until_success() -> None:
    operation = Flaky(failures=2)

    assert await retry_on_conflict(operation, attempts=3, base_delay=0) == "ok"
    assert operation.calls == 3


async def test_gives_up_after_the_last_attempt_and_reraises_the_conflict() -> None:
    operation = Flaky(failures=10)

    with pytest.raises(ConcurrentUpdateError):
        await retry_on_conflict(operation, attempts=3, base_delay=0)

    assert operation.calls == 3


async def test_other_errors_are_not_retried() -> None:
    calls = 0

    async def broken() -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("не конфликт")

    with pytest.raises(RuntimeError):
        await retry_on_conflict(broken, attempts=5, base_delay=0)

    assert calls == 1


async def test_single_attempt_means_no_retry() -> None:
    operation = Flaky(failures=1)

    with pytest.raises(ConcurrentUpdateError):
        await retry_on_conflict(operation, attempts=1, base_delay=0)

    assert operation.calls == 1


async def test_zero_attempts_is_rejected() -> None:
    with pytest.raises(ValueError, match="attempts"):
        await retry_on_conflict(Flaky(0), attempts=0)
