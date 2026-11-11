import logging
import uuid
from math import ceil

from redis.exceptions import RedisError

from pulse.core.rate_limit import RateLimitResult
from pulse.repositories.redis_client import RedisClient

logger = logging.getLogger("pulse.rate_limit")

# Всё в одном скрипте, потому что «проверить и записать» должно быть неделимым: два
# процесса, прочитавшие счётчик одновременно, оба пропустили бы запрос сверх лимита.
# Redis выполняет скрипт целиком, не вклиниваясь между его командами.
# Время берётся у самого Redis (TIME), а не у приложения: у процессов на разных
# машинах часы расходятся, и общее окно «поплыло» бы.
#
# Скользящее окно хранится как sorted set: оценка (score) это время запроса в мс,
# значение это уникальный id запроса (иначе два запроса в одну миллисекунду
# слились бы в один элемент).
_SLIDING_WINDOW = """
local key = KEYS[1]
local limit = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
local member = ARGV[3]

local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)

redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window_ms)
if redis.call('ZCARD', key) >= limit then
    local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
    return {0, tonumber(oldest[2]) + window_ms - now}
end
redis.call('ZADD', key, now, member)
redis.call('PEXPIRE', key, window_ms)
return {1, 0}
"""


class RedisRateLimiter:
    """Скользящее окно «не больше N запросов за W секунд на ключ», общее для процессов.

    Алгоритм тот же, что у локального `SlidingWindowRateLimiter`: точный (в отличие от
    фиксированного окна, где на стыке минут проходит 2N запросов) и без «разрешённого
    всплеска» token bucket. Цена в памяти: N элементов на ключ, для лимита входа (5
    попыток) это ничто. Для лимита в тысячи запросов на ключ берут окно со счётчиками.

    Если Redis недоступен, запросы ПРОПУСКАЮТСЯ (fail open) с записью в лог: иначе
    падение Redis остановило бы вход всем пользователям. Цена: пока он лежит, перебор
    паролей не ограничен (кроме стоимости самого argon2). Для эндпоинтов, где
    безопасность важнее доступности, меняют на fail closed.
    """

    def __init__(
        self,
        client: RedisClient,
        max_requests: int,
        window_seconds: float,
        *,
        prefix: str = "pulse:ratelimit",
    ) -> None:
        if max_requests < 1:
            raise ValueError("max_requests должен быть не меньше 1")
        if window_seconds <= 0:
            raise ValueError("window_seconds должен быть больше 0")
        self._max_requests = max_requests
        self._window_ms = int(window_seconds * 1000)
        self._prefix = prefix
        self._script = client.register_script(_SLIDING_WINDOW)

    async def hit(self, key: str) -> RateLimitResult:
        try:
            allowed, retry_ms = await self._script(
                keys=[f"{self._prefix}:{key}"],
                args=[self._max_requests, self._window_ms, uuid.uuid4().hex],
            )
        except (RedisError, OSError):
            logger.error("Redis недоступен, лимит не применён", exc_info=True)
            return RateLimitResult(True, 0)
        if allowed:
            return RateLimitResult(True, 0)
        return RateLimitResult(False, max(ceil(int(retry_ms) / 1000), 1))
