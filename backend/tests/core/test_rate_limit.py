import pytest

from pulse.core.rate_limit import SlidingWindowRateLimiter


class Clock:
    """Монотонные часы, которые двигает тест."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


def limiter(
    clock: Clock, max_requests: int = 3, window: float = 60
) -> SlidingWindowRateLimiter:
    return SlidingWindowRateLimiter(max_requests, window, clock=clock)


def test_requests_within_the_limit_are_allowed(clock: Clock) -> None:
    rl = limiter(clock)

    results = [rl.hit("ip") for _ in range(3)]

    assert [r.allowed for r in results] == [True, True, True]
    assert [r.retry_after for r in results] == [0, 0, 0]


def test_request_over_the_limit_is_rejected(clock: Clock) -> None:
    rl = limiter(clock)
    for _ in range(3):
        rl.hit("ip")

    result = rl.hit("ip")

    assert result.allowed is False
    assert result.retry_after == 60


def test_rejected_requests_do_not_extend_the_block(clock: Clock) -> None:
    rl = limiter(clock)
    for _ in range(3):
        rl.hit("ip")
    clock.now += 30
    for _ in range(10):
        assert rl.hit("ip").allowed is False

    clock.now += 30  # прошло 60 с от первых трёх запросов

    assert rl.hit("ip").allowed is True


def test_retry_after_counts_down_to_the_oldest_hit(clock: Clock) -> None:
    rl = limiter(clock)
    for _ in range(3):
        rl.hit("ip")

    clock.now += 45

    assert rl.hit("ip").retry_after == 15


def test_retry_after_is_rounded_up_not_down(clock: Clock) -> None:
    rl = limiter(clock)
    for _ in range(3):
        rl.hit("ip")

    clock.now += 58.5  # осталось 1.5 с: ждать надо 2, а не 1

    assert rl.hit("ip").retry_after == 2


def test_retry_after_is_rounded_up_and_at_least_one(clock: Clock) -> None:
    rl = limiter(clock)
    for _ in range(3):
        rl.hit("ip")

    clock.now += 59.2
    assert rl.hit("ip").retry_after == 1  # осталось 0.8 с, округляем вверх

    clock.now += 0.7  # осталось 0.1 с
    assert rl.hit("ip").retry_after == 1


def test_window_slides_instead_of_resetting(clock: Clock) -> None:
    rl = limiter(clock, max_requests=2, window=10)
    rl.hit("ip")  # t = 0
    clock.now += 6
    rl.hit("ip")  # t = 6
    clock.now += 5  # t = 11: первая метка (0) вышла из окна, вторая (6) ещё нет

    assert rl.hit("ip").allowed is True  # третий запрос, но в окне только один
    assert rl.hit("ip").allowed is False  # теперь в окне два (6 и 11)


def test_no_double_burst_at_the_boundary(clock: Clock) -> None:
    # Фиксированное окно пропустило бы 2N запросов подряд на стыке окон.
    rl = limiter(clock, max_requests=3, window=60)
    clock.now += 59
    for _ in range(3):
        assert rl.hit("ip").allowed is True
    clock.now += 2  # уже «следующая минута», но прошло всего 2 секунды

    assert rl.hit("ip").allowed is False


def test_hit_exactly_at_window_edge_is_free(clock: Clock) -> None:
    rl = limiter(clock, max_requests=1, window=10)
    rl.hit("ip")
    clock.now += 10

    assert rl.hit("ip").allowed is True


def test_just_before_window_edge_is_still_limited(clock: Clock) -> None:
    rl = limiter(clock, max_requests=1, window=10)
    rl.hit("ip")
    clock.now += 9.99

    assert rl.hit("ip").allowed is False


def test_keys_are_limited_independently(clock: Clock) -> None:
    rl = limiter(clock, max_requests=1)
    rl.hit("a")

    assert rl.hit("a").allowed is False
    assert rl.hit("b").allowed is True


def test_limit_of_one(clock: Clock) -> None:
    rl = limiter(clock, max_requests=1)

    assert (rl.hit("ip").allowed, rl.hit("ip").allowed) == (True, False)


@pytest.mark.parametrize(
    ("max_requests", "window"), [(0, 60), (-1, 60), (1, 0), (1, -5)]
)
def test_invalid_configuration_is_rejected(max_requests: int, window: float) -> None:
    with pytest.raises(ValueError, match="должен быть"):
        SlidingWindowRateLimiter(max_requests, window)


def test_stale_keys_are_swept_when_the_table_is_full(clock: Clock) -> None:
    rl = SlidingWindowRateLimiter(1, 10, clock=clock, max_keys=3)
    for key in ("a", "b", "c"):
        rl.hit(key)
    clock.now += 11  # все три записи устарели

    rl.hit("d")  # таблица полна: перед вставкой чистим

    # Старые ключи забыты: они снова проходят без отказа (и это не утечка памяти).
    assert rl.hit("a").allowed is True


def test_sweep_frees_memory_of_stale_keys(clock: Clock) -> None:
    rl = SlidingWindowRateLimiter(1, 10, clock=clock, max_keys=3)
    for key in ("a", "b", "c"):
        rl.hit(key)
    clock.now += 11

    rl.hit("d")

    # Без чистки таблица росла бы с каждым новым ключом: 4 записи вместо 1.
    assert len(rl._hits) == 1  # pyright: ignore[reportPrivateUsage]


def test_sweep_keeps_active_keys(clock: Clock) -> None:
    rl = SlidingWindowRateLimiter(1, 10, clock=clock, max_keys=2)
    rl.hit("old")
    clock.now += 11
    rl.hit("active")
    rl.hit("third")  # таблица полна: чистится только устаревший "old"

    assert rl.hit("active").allowed is False


def test_default_clock_is_monotonic_and_works() -> None:
    rl = SlidingWindowRateLimiter(1, 60)

    assert rl.hit("ip").allowed is True
    assert rl.hit("ip").allowed is False
