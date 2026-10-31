import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher
from pydantic import SecretStr

from pulse.core.passwords import PasswordHasher
from pulse.domain.errors import (
    DuplicateUserError,
    InvalidCredentialsError,
    InvalidTokenError,
    TokenExpiredError,
)
from pulse.domain.users import MAX_PASSWORD_LENGTH
from pulse.repositories.users import UserRepository
from pulse.services.auth import AuthService
from pulse.services.tokens import TokenService

NOW = datetime(2026, 1, 1, tzinfo=UTC)
PASSWORD = "correct horse battery"


class SpyHasher(PasswordHasher):
    """Настоящий (дешёвый) хешер, который считает вызовы."""

    def __init__(self) -> None:
        super().__init__(
            PasswordHash((Argon2Hasher(time_cost=1, memory_cost=8, parallelism=1),))
        )
        self.hash_calls = 0
        self.verify_calls = 0
        self.dummy_calls = 0

    async def hash(self, password: str) -> str:
        self.hash_calls += 1
        return await super().hash(password)

    async def verify(self, password: str, hashed: str) -> bool:
        self.verify_calls += 1
        return await super().verify(password, hashed)

    async def verify_dummy(self, password: str) -> None:
        self.dummy_calls += 1
        await super().verify_dummy(password)


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def users() -> UserRepository:
    return UserRepository()


@pytest.fixture
def hasher() -> SpyHasher:
    return SpyHasher()


@pytest.fixture
def auth(users: UserRepository, hasher: SpyHasher, clock: Clock) -> AuthService:
    tokens = TokenService(
        SecretStr("service-test-secret-long-enough-for-hs256"),
        timedelta(minutes=15),
        clock,
    )
    return AuthService(users, hasher, tokens)


# --- register --------------------------------------------------------------


async def test_register_creates_a_user(
    auth: AuthService, users: UserRepository
) -> None:
    user = await auth.register("alice@example.com", PASSWORD)

    assert user.id == 1
    assert users.get(user.id) == user


async def test_register_stores_a_hash_not_the_password(auth: AuthService) -> None:
    user = await auth.register("alice@example.com", PASSWORD)

    assert PASSWORD not in user.password_hash
    assert user.password_hash.startswith("$argon2id$")


async def test_register_normalizes_email(auth: AuthService) -> None:
    user = await auth.register("  Alice@Example.COM ", PASSWORD)

    assert user.email == "alice@example.com"


@pytest.mark.parametrize(
    "again", ["alice@example.com", "ALICE@example.com", " alice@EXAMPLE.com "]
)
async def test_register_rejects_duplicate_email_in_any_form(
    auth: AuthService, users: UserRepository, again: str
) -> None:
    await auth.register("alice@example.com", PASSWORD)

    with pytest.raises(DuplicateUserError):
        await auth.register(again, "another password")

    assert users.find_by_email("alice@example.com") is not None
    assert users.get(2) is None


async def test_concurrent_registrations_of_one_email_create_one_user(
    auth: AuthService, users: UserRepository
) -> None:
    # Хеширование это await: за это время такой же запрос успевает проверить email.
    results = await asyncio.gather(
        *(auth.register("race@example.com", PASSWORD) for _ in range(5)),
        return_exceptions=True,
    )

    created = [r for r in results if not isinstance(r, BaseException)]
    errors = [r for r in results if isinstance(r, DuplicateUserError)]
    assert len(created) == 1
    assert len(errors) == 4
    assert users.get(2) is None


async def test_same_password_gives_different_hashes(auth: AuthService) -> None:
    first = await auth.register("a@example.com", PASSWORD)
    second = await auth.register("b@example.com", PASSWORD)

    assert first.password_hash != second.password_hash


# --- login -----------------------------------------------------------------


async def test_login_returns_a_token_for_the_user(auth: AuthService) -> None:
    user = await auth.register("alice@example.com", PASSWORD)

    token = await auth.login("alice@example.com", PASSWORD)

    assert token.expires_in == 900
    assert auth.authenticate(token.token) == user


@pytest.mark.parametrize("email", ["ALICE@example.com", "  alice@example.com  "])
async def test_login_normalizes_email(auth: AuthService, email: str) -> None:
    await auth.register("alice@example.com", PASSWORD)

    assert (await auth.login(email, PASSWORD)).token


@pytest.mark.parametrize(
    "wrong", ["wrong password", PASSWORD.upper(), PASSWORD + " ", ""]
)
async def test_login_with_wrong_password_fails(auth: AuthService, wrong: str) -> None:
    await auth.register("alice@example.com", PASSWORD)

    with pytest.raises(InvalidCredentialsError):
        await auth.login("alice@example.com", wrong)


async def test_login_with_unknown_email_fails_with_the_same_error(
    auth: AuthService,
) -> None:
    await auth.register("alice@example.com", PASSWORD)

    with pytest.raises(InvalidCredentialsError) as unknown:
        await auth.login("nobody@example.com", PASSWORD)
    with pytest.raises(InvalidCredentialsError) as wrong:
        await auth.login("alice@example.com", "wrong password")

    assert (unknown.value.code, str(unknown.value)) == (
        wrong.value.code,
        str(wrong.value),
    )


async def test_unknown_email_still_spends_hashing_time(
    auth: AuthService, hasher: SpyHasher
) -> None:
    await auth.register("alice@example.com", PASSWORD)

    with pytest.raises(InvalidCredentialsError):
        await auth.login("nobody@example.com", PASSWORD)

    assert hasher.dummy_calls == 1


async def test_known_email_does_not_use_the_dummy(
    auth: AuthService, hasher: SpyHasher
) -> None:
    await auth.register("alice@example.com", PASSWORD)
    await auth.login("alice@example.com", PASSWORD)

    assert hasher.dummy_calls == 0
    assert hasher.verify_calls == 1


async def test_too_long_password_is_rejected_without_hashing(
    auth: AuthService, hasher: SpyHasher
) -> None:
    await auth.register("alice@example.com", PASSWORD)
    before = hasher.verify_calls

    with pytest.raises(InvalidCredentialsError):
        await auth.login("alice@example.com", "x" * (MAX_PASSWORD_LENGTH + 1))

    assert hasher.verify_calls == before
    assert hasher.dummy_calls == 0


async def test_password_of_max_length_is_checked_normally(auth: AuthService) -> None:
    password = "p" * MAX_PASSWORD_LENGTH
    await auth.register("alice@example.com", password)

    assert (await auth.login("alice@example.com", password)).token


# --- authenticate ----------------------------------------------------------


async def test_authenticate_returns_the_user(auth: AuthService) -> None:
    user = await auth.register("alice@example.com", PASSWORD)
    token = await auth.login("alice@example.com", PASSWORD)

    assert auth.authenticate(token.token) == user


async def test_authenticate_rejects_expired_token(
    auth: AuthService, clock: Clock
) -> None:
    await auth.register("alice@example.com", PASSWORD)
    token = await auth.login("alice@example.com", PASSWORD)
    clock.now += timedelta(minutes=15)

    with pytest.raises(TokenExpiredError):
        auth.authenticate(token.token)


async def test_authenticate_rejects_garbage(auth: AuthService) -> None:
    with pytest.raises(InvalidTokenError):
        auth.authenticate("not-a-token")


async def test_token_of_a_vanished_user_is_rejected(auth: AuthService) -> None:
    # Валидная подпись, но пользователя с таким id нет (например, удалён).
    other = AuthService(
        UserRepository(),
        SpyHasher(),
        TokenService(
            SecretStr("service-test-secret-long-enough-for-hs256"),
            timedelta(minutes=15),
            lambda: NOW,
        ),
    )
    await other.register("ghost@example.com", PASSWORD)
    token = await other.login("ghost@example.com", PASSWORD)

    with pytest.raises(InvalidTokenError):
        auth.authenticate(token.token)
