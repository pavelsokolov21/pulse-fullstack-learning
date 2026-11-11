"""Лимитер на Redis: скользящее окно, атомарность, общий для процессов."""

import asyncio
import logging
import os
import sys

import pytest

from pulse.repositories.redis_client import RedisClient, create_redis
from pulse.repositories.redis_rate_limiter import RedisRateLimiter

# Сколько запросов делает каждый процесс и сколько всего разрешено.
PROCESSES = 4
PER_PROCESS = 10
LIMIT = 10

# Отдельный процесс Python: свой клиент Redis, своя память, общее у всех только
# содержимое Redis. Печатает, сколько запросов из PER_PROCESS прошло.
WORKER = """
import asyncio, sys
from pulse.core.rate_limit import InMemoryRateLimiter, SlidingWindowRateLimiter
from pulse.repositories.redis_client import create_redis
from pulse.repositories.redis_rate_limiter import RedisRateLimiter

async def main(url, mode, limit, attempts):
    if mode == "redis":
        client = create_redis(url)
        limiter = RedisRateLimiter(client, limit, 60, prefix="proc")
    else:
        client = None
        limiter = InMemoryRateLimiter(SlidingWindowRateLimiter(limit, 60))
    allowed = 0
    for _ in range(attempts):
        if (await limiter.hit("shared-key")).allowed:
            allowed += 1
    if client is not None:
        await client.aclose()
    print(allowed)

asyncio.run(main(sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])))
"""


@pytest.fixture
def limiter(redis_client: RedisClient) -> RedisRateLimiter:
    return RedisRateLimiter(redis_client, 3, 60)


async def test_allows_requests_up_to_the_limit(limiter: RedisRateLimiter) -> None:
    results = [(await limiter.hit("ip")).allowed for _ in range(3)]

    assert results == [True, True, True]


async def test_rejects_the_request_over_the_limit_with_retry_after(
    limiter: RedisRateLimiter,
) -> None:
    for _ in range(3):
        await limiter.hit("ip")

    result = await limiter.hit("ip")

    assert not result.allowed
    assert 1 <= result.retry_after <= 60


async def test_rejected_requests_are_not_counted(limiter: RedisRateLimiter) -> None:
    # Отказ не продлевает блокировку: окно считает только пропущенные запросы.
    for _ in range(3):
        await limiter.hit("ip")
    first = await limiter.hit("ip")
    for _ in range(10):
        await limiter.hit("ip")
    last = await limiter.hit("ip")

    assert last.retry_after <= first.retry_after


async def test_keys_are_independent(limiter: RedisRateLimiter) -> None:
    for _ in range(3):
        await limiter.hit("a")

    assert not (await limiter.hit("a")).allowed
    assert (await limiter.hit("b")).allowed


async def test_window_slides_instead_of_resetting(
    redis_client: RedisClient,
) -> None:
    # Окно 1 с, лимит 2. Первый запрос уходит из окна раньше второго.
    limiter = RedisRateLimiter(redis_client, 2, 1.0)
    assert (await limiter.hit("ip")).allowed  # t = 0
    await asyncio.sleep(0.6)
    assert (await limiter.hit("ip")).allowed  # t = 0.6
    assert not (await limiter.hit("ip")).allowed  # окно полно
    await asyncio.sleep(0.6)  # t = 1.2: первый вышел, второй ещё внутри

    assert (await limiter.hit("ip")).allowed
    assert not (await limiter.hit("ip")).allowed  # теперь снова полно: 2 в окне


async def test_key_expires_by_itself_after_the_window(
    redis_client: RedisClient,
) -> None:
    limiter = RedisRateLimiter(redis_client, 1, 1.0, prefix="p")
    await limiter.hit("ip")

    assert 0 < await redis_client.pttl("p:ip") <= 1000
    await asyncio.sleep(1.1)

    assert await redis_client.exists("p:ip") == 0


async def test_parallel_requests_never_exceed_the_limit(
    redis_client: RedisClient,
) -> None:
    # 50 запросов в один момент: проверка и запись неделимы, лишнего не пройдёт.
    limiter = RedisRateLimiter(redis_client, 10, 60)

    results = await asyncio.gather(*(limiter.hit("ip") for _ in range(50)))

    assert sum(r.allowed for r in results) == 10


async def test_two_limiters_on_one_redis_share_the_counter(
    redis_client: RedisClient,
) -> None:
    first = RedisRateLimiter(redis_client, 4, 60)
    second = RedisRateLimiter(redis_client, 4, 60)

    allowed = [(await first.hit("ip")).allowed for _ in range(3)]
    allowed += [(await second.hit("ip")).allowed for _ in range(3)]

    assert allowed == [True] * 4 + [False] * 2


async def run_workers(mode: str) -> list[int]:
    processes = [
        await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            WORKER,
            os.environ["PULSE_API_REDIS_URL"],
            mode,
            str(LIMIT),
            str(PER_PROCESS),
            stdout=asyncio.subprocess.PIPE,
        )
        for _ in range(PROCESSES)
    ]
    outputs = await asyncio.gather(*(p.communicate() for p in processes))
    assert all(p.returncode == 0 for p in processes)
    return [int(out.decode().strip()) for out, _ in outputs]


async def test_limit_is_shared_between_separate_processes(
    redis_client: RedisClient,
) -> None:
    # 4 процесса по 10 попыток = 40 запросов на один ключ; разрешено 10 на всех.
    allowed = await run_workers("redis")

    assert sum(allowed) == LIMIT


async def test_in_memory_limiter_is_not_shared_between_processes() -> None:
    # Контроль: тот же сценарий с лимитом в памяти. Каждый процесс считает сам, и
    # реальный лимит выходит в число процессов раз выше. Это и есть причина Redis.
    allowed = await run_workers("memory")

    assert allowed == [LIMIT] * PROCESSES


async def test_redis_outage_lets_requests_through_and_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    dead = create_redis("redis://:x@127.0.0.1:1/0")
    limiter = RedisRateLimiter(dead, 1, 60)

    with caplog.at_level(logging.ERROR, logger="pulse.rate_limit"):
        results = [await limiter.hit("ip") for _ in range(3)]
    await dead.aclose()

    assert all(r.allowed for r in results)
    assert "Redis недоступен" in caplog.text


@pytest.mark.parametrize(
    ("max_requests", "window"), [(0, 60), (-1, 60), (1, 0), (1, -5)]
)
def test_rejects_invalid_arguments(
    redis_client: RedisClient, max_requests: int, window: float
) -> None:
    with pytest.raises(ValueError, match="должен быть"):
        RedisRateLimiter(redis_client, max_requests, window)
