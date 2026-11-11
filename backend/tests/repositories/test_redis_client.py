import os
import re

import pytest
from redis.exceptions import RedisError

from pulse.repositories.redis_client import RedisClient, create_redis, ping


def test_url_prefixes_are_redis_schemes() -> None:
    from pulse.repositories.redis_client import REDIS_URL_PREFIXES

    assert REDIS_URL_PREFIXES == ("redis://", "rediss://")


async def test_ping_succeeds_with_the_right_password(
    redis_client: RedisClient,
) -> None:
    await ping(redis_client)


async def test_ping_fails_with_a_wrong_password() -> None:
    wrong = re.sub(r":[^:@]*@", ":wrong-password@", os.environ["PULSE_API_REDIS_URL"])
    client = create_redis(wrong)

    try:
        with pytest.raises(RedisError):
            await ping(client)
    finally:
        await client.aclose()


async def test_ping_fails_quickly_when_nothing_listens() -> None:
    client = create_redis("redis://:x@127.0.0.1:1/0")

    try:
        with pytest.raises((RedisError, OSError)):
            await ping(client)
    finally:
        await client.aclose()


async def test_responses_are_strings_not_bytes(redis_client: RedisClient) -> None:
    await redis_client.set("k", "значение")

    assert await redis_client.get("k") == "значение"
