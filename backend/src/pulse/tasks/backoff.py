import random
from collections.abc import Callable

BASE_SECONDS = 2.0
CAP_SECONDS = 300.0


def backoff_delay(
    retries: int,
    *,
    retry_after: float | None = None,
    base: float = BASE_SECONDS,
    cap: float = CAP_SECONDS,
    jitter: Callable[[], float] = random.random,
) -> float:
    """Через сколько секунд повторить задачу после `retries` неудачных попыток.

    Экспонента: base, 2*base, 4*base, ... до потолка `cap`. Рост нужен, чтобы лежащий
    сервис не добивали частыми повторами. Джиттер («равный»: от половины до целого)
    нужен, чтобы сотня задач, упавших одновременно, не пришла на повтор одновременно
    и снова не уронила сервис (thundering herd).

    `retry_after`: сервер сам просил подождать (429/503 + Retry-After). Ждём не меньше,
    но не дольше потолка: чужой заголовок не должен усыпить задачу на сутки.
    """
    exponential = min(cap, base * 2**retries)
    delay = exponential / 2 + jitter() * exponential / 2
    if retry_after is not None:
        delay = max(delay, min(retry_after, cap))
    return delay
