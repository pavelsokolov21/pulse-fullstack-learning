import asyncio
import random
from collections.abc import Awaitable, Callable

from pulse.domain.errors import ConcurrentUpdateError

DEFAULT_ATTEMPTS = 5
DEFAULT_BASE_DELAY = 0.02  # секунды


async def retry_on_conflict[T](
    operation: Callable[[], Awaitable[T]],
    *,
    attempts: int = DEFAULT_ATTEMPTS,
    base_delay: float = DEFAULT_BASE_DELAY,
) -> T:
    """Повторяет `operation` при `ConcurrentUpdateError`, не больше `attempts` раз.

    Вызывающий код оборачивает целую операцию: каждая попытка обязана открыть СВОЮ
    сессию, иначе в identity map останется устаревшая строка и конфликт повторится.
    Повторяется только `ConcurrentUpdateError`: после него ничего не записано, так что
    повтор безопасен. Любая другая ошибка уходит наружу сразу. Пауза растёт
    экспоненциально со случайным разбросом (jitter), чтобы конкуренты не сталкивались
    снова в один и тот же момент.
    """
    if attempts < 1:
        raise ValueError("attempts должен быть не меньше 1")
    attempt = 1
    while True:
        try:
            return await operation()
        except ConcurrentUpdateError:
            if attempt >= attempts:
                raise
        await asyncio.sleep(random.uniform(0, base_delay * 2**attempt))
        attempt += 1
