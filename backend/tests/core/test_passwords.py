import asyncio
import threading
import time
from collections.abc import Awaitable

import pytest
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher

from pulse.core.passwords import PasswordHasher


def cheap_hash() -> PasswordHash:
    return PasswordHash((Argon2Hasher(time_cost=1, memory_cost=8, parallelism=1),))


@pytest.fixture
def hasher() -> PasswordHasher:
    return PasswordHasher(cheap_hash())


async def test_hash_is_not_the_password_and_is_salted(hasher: PasswordHasher) -> None:
    first = await hasher.hash("correct horse")
    second = await hasher.hash("correct horse")

    assert first != "correct horse"
    assert first != second


async def test_verify_accepts_the_right_password(hasher: PasswordHasher) -> None:
    hashed = await hasher.hash("correct horse")

    assert await hasher.verify("correct horse", hashed) is True


@pytest.mark.parametrize("wrong", ["Correct horse", "correct horse ", "", "x"])
async def test_verify_rejects_other_passwords(
    hasher: PasswordHasher, wrong: str
) -> None:
    hashed = await hasher.hash("correct horse")

    assert await hasher.verify(wrong, hashed) is False


async def test_default_algorithm_is_argon2id() -> None:
    hashed = await PasswordHasher().hash("correct horse")

    assert hashed.startswith("$argon2id$")


async def test_unicode_password_round_trip(hasher: PasswordHasher) -> None:
    hashed = await hasher.hash("пароль-🔑-密码")

    assert await hasher.verify("пароль-🔑-密码", hashed) is True


# --- не блокирует event loop ----------------------------------------------


class SlowHash:
    """Подмена PasswordHash: долго «хеширует» и считает одновременные вызовы."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.active = 0
        self.peak = 0
        self.thread_names: list[str] = []
        self.verify_threads: list[str] = []

    def hash(self, password: str) -> str:
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
            self.thread_names.append(threading.current_thread().name)
        time.sleep(0.05)
        with self.lock:
            self.active -= 1
        return f"hash:{password}"

    def verify(self, password: str, hashed: str) -> bool:
        with self.lock:
            self.verify_threads.append(threading.current_thread().name)
        time.sleep(0.05)
        return hashed == f"hash:{password}"


def make_slow(max_concurrent: int = 4) -> tuple[PasswordHasher, SlowHash]:
    slow = SlowHash()
    return PasswordHasher(slow, max_concurrent=max_concurrent), slow  # pyright: ignore[reportArgumentType]


async def test_hashing_runs_outside_the_event_loop_thread() -> None:
    hasher, slow = make_slow()

    await hasher.hash("pw")

    assert slow.thread_names
    assert threading.current_thread().name not in slow.thread_names


async def count_ticks_during(work: Awaitable[object]) -> int:
    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.005)
            ticks += 1

    task = asyncio.create_task(ticker())
    await work
    task.cancel()
    return ticks


async def test_event_loop_stays_responsive_while_hashing() -> None:
    hasher, _ = make_slow()

    # Блокирующий вызов в цикле дал бы 0 тиков за 50 мс.
    assert await count_ticks_during(hasher.hash("pw")) >= 3


async def test_event_loop_stays_responsive_while_verifying() -> None:
    hasher, _ = make_slow()

    assert await count_ticks_during(hasher.verify("pw", "hash:pw")) >= 3


async def test_verification_runs_outside_the_event_loop_thread() -> None:
    hasher, slow = make_slow()

    await hasher.verify("pw", "hash:pw")

    assert slow.verify_threads
    assert threading.current_thread().name not in slow.verify_threads


async def test_concurrent_hashing_is_limited() -> None:
    hasher, slow = make_slow(max_concurrent=2)

    await asyncio.gather(*(hasher.hash(f"pw{n}") for n in range(8)))

    assert slow.peak == 2


async def test_verify_shares_the_same_limit() -> None:
    hasher, slow = make_slow(max_concurrent=1)

    async def checks() -> None:
        await asyncio.gather(
            hasher.hash("a"), hasher.hash("b"), hasher.verify("a", "hash:a")
        )

    await checks()

    assert slow.peak == 1


@pytest.mark.parametrize("limit", [0, -1])
def test_non_positive_limit_is_rejected(limit: int) -> None:
    with pytest.raises(ValueError, match="max_concurrent"):
        PasswordHasher(cheap_hash(), max_concurrent=limit)


# --- verify_dummy ----------------------------------------------------------


async def test_verify_dummy_returns_nothing_and_costs_a_verify(
    hasher: PasswordHasher,
) -> None:
    result = await hasher.verify_dummy("any password")

    assert result is None


async def test_verify_dummy_computes_its_hash_once() -> None:
    hasher, slow = make_slow()

    await hasher.verify_dummy("a")
    await hasher.verify_dummy("b")

    assert len(slow.thread_names) == 1  # hash вызван один раз, дальше только verify
