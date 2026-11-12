import pytest

from pulse.tasks.backoff import backoff_delay


def no_jitter_low() -> float:
    return 0.0


def no_jitter_high() -> float:
    return 1.0


def test_delay_doubles_with_every_failed_attempt() -> None:
    upper = [
        backoff_delay(n, base=2.0, cap=1000.0, jitter=no_jitter_high) for n in range(5)
    ]

    assert upper == [2.0, 4.0, 8.0, 16.0, 32.0]


def test_jitter_spreads_the_delay_between_half_and_full() -> None:
    low = backoff_delay(3, base=2.0, cap=1000.0, jitter=no_jitter_low)
    high = backoff_delay(3, base=2.0, cap=1000.0, jitter=no_jitter_high)

    assert (low, high) == (8.0, 16.0)


def test_delay_is_capped() -> None:
    assert backoff_delay(20, base=2.0, cap=300.0, jitter=no_jitter_high) == 300.0


def test_default_jitter_stays_in_range() -> None:
    delays = {backoff_delay(4, base=2.0, cap=1000.0) for _ in range(200)}

    assert all(16.0 <= d <= 32.0 for d in delays)
    assert len(delays) > 1  # случайность настоящая, а не константа


def test_retry_after_from_the_server_raises_the_delay() -> None:
    delay = backoff_delay(0, retry_after=120.0, base=2.0, jitter=no_jitter_high)

    assert delay == 120.0


def test_small_retry_after_does_not_shorten_the_delay() -> None:
    delay = backoff_delay(3, retry_after=1.0, base=2.0, jitter=no_jitter_high)

    assert delay == 16.0


def test_retry_after_is_capped_so_a_hostile_header_cannot_park_the_task() -> None:
    delay = backoff_delay(0, retry_after=86_400.0, cap=300.0, jitter=no_jitter_high)

    assert delay == 300.0


@pytest.mark.parametrize("retries", [0, 1, 5])
def test_delay_is_always_positive(retries: int) -> None:
    assert backoff_delay(retries, jitter=no_jitter_low) > 0
