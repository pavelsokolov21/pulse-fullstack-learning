"""Подключение к Redis: один асинхронный клиент на процесс.

Как и `database.py`, всё, что знает про драйвер Redis, живёт в слое репозиториев.
Клиент создаётся при запуске приложения (`lifespan`) и закрывается при остановке:
внутри пул соединений, и создавать его на каждый запрос нельзя.
"""

from redis.asyncio import Redis

REDIS_URL_PREFIXES = ("redis://", "rediss://")

# Секунды. Без таймаутов зависший Redis подвесил бы каждый запрос, который к нему
# обратился (а кеш и лимитер обращаются на каждый). С ними запрос ждёт не дольше.
CONNECT_TIMEOUT = 2.0
COMMAND_TIMEOUT = 2.0

type RedisClient = Redis


def create_redis(url: str) -> RedisClient:
    """Клиент с пулом соединений. Само подключение откладывается до первой команды."""
    # У `from_url` в redis-py нетипизированные **kwargs: pyright strict их не принимает.
    return Redis.from_url(  # pyright: ignore[reportUnknownMemberType]
        url,
        # Ответы приходят строками, а не bytes: ключи и значения у нас текстовые.
        decode_responses=True,
        socket_connect_timeout=CONNECT_TIMEOUT,
        socket_timeout=COMMAND_TIMEOUT,
        # Раз в 30 с проверяет простаивающее соединение: сервер или сеть могли
        # закрыть его молча, и первая команда после паузы упала бы.
        health_check_interval=30,
    )


async def ping(client: RedisClient) -> None:
    """Проверяет, что Redis доступен и пароль верный; иначе бросает исключение."""
    if not await client.ping():  # pyright: ignore[reportUnknownMemberType]
        raise ConnectionError("Redis не ответил на PING")
