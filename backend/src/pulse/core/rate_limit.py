import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from math import ceil
from typing import Protocol

type MonotonicClock = Callable[[], float]

DEFAULT_MAX_KEYS = 10_000


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    retry_after: int  # секунды до освобождения слота; 0, если запрос разрешён


class SlidingWindowRateLimiter:
    """Ограничитель «не больше N запросов за последние W секунд» на ключ.

    Для каждого ключа хранятся метки времени разрешённых запросов в `deque`.
    Запрос проходит, если в окне меньше `max_requests` меток. Это точнее
    фиксированных окон: там на стыке минут можно сделать 2N запросов подряд.

    Часы монотонные (`time.monotonic`): перевод системного времени не ломает окно.
    Не потокобезопасен, рассчитан на вызовы из одного потока event loop.
    """

    def __init__(
        self,
        max_requests: int,
        window_seconds: float,
        *,
        clock: MonotonicClock = time.monotonic,
        max_keys: int = DEFAULT_MAX_KEYS,
    ) -> None:
        if max_requests < 1:
            raise ValueError("max_requests должен быть не меньше 1")
        if window_seconds <= 0:
            raise ValueError("window_seconds должен быть больше 0")
        self._max_requests = max_requests
        self._window = window_seconds
        self._clock = clock
        self._max_keys = max_keys
        self._hits: dict[str, deque[float]] = {}

    def hit(self, key: str) -> RateLimitResult:
        """Засчитывает запрос, если лимит не исчерпан, и сообщает результат."""
        now = self._clock()
        if len(self._hits) >= self._max_keys and key not in self._hits:
            self._sweep(now)

        window = self._hits.setdefault(key, deque())
        self._drop_expired(window, now)

        if len(window) >= self._max_requests:
            # Слот освободится, когда выпадет самая старая метка.
            retry_after = ceil(window[0] + self._window - now)
            return RateLimitResult(False, max(retry_after, 1))

        window.append(now)
        return RateLimitResult(True, 0)

    def _drop_expired(self, window: deque[float], now: float) -> None:
        while window and window[0] <= now - self._window:
            window.popleft()

    def _sweep(self, now: float) -> None:
        # Без этого ключи «одноразовых» клиентов копились бы бесконечно.
        for key, window in list(self._hits.items()):
            self._drop_expired(window, now)
            if not window:
                del self._hits[key]


class RateLimiter(Protocol):
    """Ограничитель частоты, каким его видит API: асинхронный, чтобы можно было
    подставить и локальный (в памяти процесса), и общий (Redis)."""

    async def hit(self, key: str) -> RateLimitResult: ...


class InMemoryRateLimiter:
    """Локальный ограничитель в интерфейсе `RateLimiter`.

    Счётчики живут в памяти одного процесса: при нескольких процессах сервера у
    каждого свои, и реальный лимит получается в N раз выше. Для общего лимита нужен
    Redis (`pulse.repositories.redis_rate_limiter`).
    """

    def __init__(self, limiter: SlidingWindowRateLimiter) -> None:
        self._limiter = limiter

    async def hit(self, key: str) -> RateLimitResult:
        return self._limiter.hit(key)
