from datetime import UTC, datetime, timedelta

import pytest

from pulse.domain.errors import (
    InvalidRefreshTokenError,
    RefreshTokenError,
    RefreshTokenExpiredError,
)
from pulse.domain.users import Role, User
from pulse.repositories.database import Session
from pulse.repositories.refresh_tokens import RefreshTokenRepository
from pulse.repositories.users import UserRepository
from pulse.services.refresh_tokens import RefreshTokenService, hash_token

NOW = datetime(2026, 1, 1, tzinfo=UTC)
TTL = timedelta(days=7)


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def repo(session: Session) -> RefreshTokenRepository:
    return RefreshTokenRepository(session)


@pytest.fixture
async def user(session: Session) -> User:
    """Владелец токенов: внешний ключ требует настоящую строку в users."""
    return await UserRepository(session).create(
        email="user@example.com", password_hash="h", role=Role.MANAGER
    )


@pytest.fixture
def service(repo: RefreshTokenRepository, clock: Clock) -> RefreshTokenService:
    return RefreshTokenService(repo, TTL, clock)


# --- issue -----------------------------------------------------------------


async def test_issue_returns_a_random_token_and_lifetime(
    user: User,
    service: RefreshTokenService,
) -> None:
    issued = await service.issue(user.id)

    assert issued.expires_in == 7 * 24 * 3600
    assert len(issued.token) >= 43  # 32 байта в base64url


async def test_issued_tokens_are_unique(
    user: User, service: RefreshTokenService
) -> None:
    tokens = {(await service.issue(user.id)).token for _ in range(50)}

    assert len(tokens) == 50


async def test_only_the_hash_is_stored(
    user: User, service: RefreshTokenService, repo: RefreshTokenRepository
) -> None:
    issued = await service.issue(user.id)

    record = await repo.find_by_hash(hash_token(issued.token))
    assert record is not None
    assert record.token_hash != issued.token
    assert issued.token not in record.token_hash
    assert await repo.find_by_hash(issued.token) is None


async def test_issue_starts_a_new_family_each_time(
    user: User, service: RefreshTokenService, repo: RefreshTokenRepository
) -> None:
    first, second = await service.issue(user.id), await service.issue(user.id)

    a = await repo.find_by_hash(hash_token(first.token))
    b = await repo.find_by_hash(hash_token(second.token))
    assert a is not None
    assert b is not None
    assert a.family_id != b.family_id


async def test_expiry_is_ttl_after_issue(
    user: User, service: RefreshTokenService, repo: RefreshTokenRepository
) -> None:
    issued = await service.issue(user.id)

    record = await repo.find_by_hash(hash_token(issued.token))
    assert record is not None
    assert record.expires_at == NOW + TTL


# --- rotate ----------------------------------------------------------------


async def test_rotate_returns_user_and_a_different_token(
    user: User,
    service: RefreshTokenService,
) -> None:
    old = await service.issue(user.id)

    user_id, new = await service.rotate(old.token)

    assert user_id == user.id
    assert new.token != old.token


async def test_rotate_keeps_the_family_and_revokes_the_old_token(
    user: User, service: RefreshTokenService, repo: RefreshTokenRepository
) -> None:
    old = await service.issue(user.id)
    _, new = await service.rotate(old.token)

    old_record = await repo.find_by_hash(hash_token(old.token))
    new_record = await repo.find_by_hash(hash_token(new.token))
    assert old_record is not None
    assert new_record is not None
    assert old_record.revoked_at == NOW
    assert new_record.revoked_at is None
    assert old_record.family_id == new_record.family_id


async def test_new_token_gets_a_fresh_lifetime(
    user: User, service: RefreshTokenService, repo: RefreshTokenRepository, clock: Clock
) -> None:
    old = await service.issue(user.id)
    clock.now += timedelta(days=3)

    _, new = await service.rotate(old.token)

    record = await repo.find_by_hash(hash_token(new.token))
    assert record is not None
    assert record.expires_at == clock.now + TTL


async def test_rotation_chain_keeps_working(
    user: User, service: RefreshTokenService
) -> None:
    token = (await service.issue(user.id)).token

    for _ in range(5):
        _, new = await service.rotate(token)
        token = new.token

    assert (await service.rotate(token))[0] == user.id


async def test_unknown_token_is_rejected(service: RefreshTokenService) -> None:
    with pytest.raises(InvalidRefreshTokenError):
        await service.rotate("never-issued")


@pytest.mark.parametrize("garbage", ["", " ", "a" * 10_000, "ы"])
async def test_garbage_is_rejected(service: RefreshTokenService, garbage: str) -> None:
    with pytest.raises(InvalidRefreshTokenError):
        await service.rotate(garbage)


# --- повторное использование ------------------------------------------------


async def test_reusing_an_old_token_is_rejected(
    user: User, service: RefreshTokenService
) -> None:
    old = await service.issue(user.id)
    await service.rotate(old.token)

    with pytest.raises(InvalidRefreshTokenError):
        await service.rotate(old.token)


async def test_reuse_revokes_the_whole_chain(
    user: User, service: RefreshTokenService, repo: RefreshTokenRepository
) -> None:
    first = await service.issue(user.id)
    _, second = await service.rotate(first.token)
    _, third = await service.rotate(second.token)

    with pytest.raises(InvalidRefreshTokenError):
        await service.rotate(first.token)  # вор предъявил самый первый токен

    # Законный владелец с актуальным токеном тоже вынужден войти заново.
    with pytest.raises(InvalidRefreshTokenError):
        await service.rotate(third.token)
    family = await repo.list_family(
        (await repo.find_by_hash(hash_token(first.token))).family_id  # pyright: ignore[reportOptionalMemberAccess]
    )
    assert len(family) == 3
    assert all(t.revoked_at is not None for t in family)


async def test_reuse_does_not_touch_other_sessions(
    user: User, service: RefreshTokenService
) -> None:
    stolen = await service.issue(user.id)
    other_device = await service.issue(user.id)
    await service.rotate(stolen.token)

    with pytest.raises(InvalidRefreshTokenError):
        await service.rotate(stolen.token)

    assert (await service.rotate(other_device.token))[0] == user.id


async def test_reuse_is_reported_as_a_generic_invalid_token(
    user: User,
    service: RefreshTokenService,
) -> None:
    old = await service.issue(user.id)
    await service.rotate(old.token)

    with pytest.raises(InvalidRefreshTokenError) as reused:
        await service.rotate(old.token)
    with pytest.raises(InvalidRefreshTokenError) as unknown:
        await service.rotate("never-issued")

    assert reused.value.code == unknown.value.code
    assert str(reused.value) == str(unknown.value)


# --- срок ------------------------------------------------------------------


async def test_token_is_valid_until_the_last_second(
    user: User, service: RefreshTokenService, clock: Clock
) -> None:
    token = (await service.issue(user.id)).token
    clock.now = NOW + TTL - timedelta(seconds=1)

    assert (await service.rotate(token))[0] == user.id


@pytest.mark.parametrize("extra", [0, 1, 86400])
async def test_expired_token_is_rejected(
    user: User, service: RefreshTokenService, clock: Clock, extra: int
) -> None:
    token = (await service.issue(user.id)).token
    clock.now = NOW + TTL + timedelta(seconds=extra)

    with pytest.raises(RefreshTokenExpiredError):
        await service.rotate(token)


def test_expired_error_is_a_refresh_token_error() -> None:
    assert issubclass(RefreshTokenExpiredError, RefreshTokenError)
    assert issubclass(InvalidRefreshTokenError, RefreshTokenError)


async def test_rotation_of_expired_token_does_not_issue_a_new_one(
    user: User, service: RefreshTokenService, repo: RefreshTokenRepository, clock: Clock
) -> None:
    token = (await service.issue(user.id)).token
    clock.now += TTL

    with pytest.raises(RefreshTokenExpiredError):
        await service.rotate(token)

    family = await repo.list_family(
        (await repo.find_by_hash(hash_token(token))).family_id  # pyright: ignore[reportOptionalMemberAccess]
    )
    assert len(family) == 1


# --- revoke ----------------------------------------------------------------


async def test_revoke_ends_the_session(
    user: User, service: RefreshTokenService
) -> None:
    token = (await service.issue(user.id)).token

    await service.revoke(token)

    with pytest.raises(InvalidRefreshTokenError):
        await service.rotate(token)


async def test_revoke_kills_the_current_token_of_the_chain(
    user: User,
    service: RefreshTokenService,
) -> None:
    first = await service.issue(user.id)
    _, current = await service.rotate(first.token)

    await service.revoke(
        first.token
    )  # logout со старым токеном всё равно закрывает сессию

    with pytest.raises(InvalidRefreshTokenError):
        await service.rotate(current.token)


async def test_revoke_leaves_other_sessions_alone(
    user: User, service: RefreshTokenService
) -> None:
    mine, other = await service.issue(user.id), await service.issue(user.id)

    await service.revoke(mine.token)

    assert (await service.rotate(other.token))[0] == user.id


async def test_revoke_unknown_token_is_a_noop(service: RefreshTokenService) -> None:
    await service.revoke("never-issued")


async def test_revoke_twice_is_fine(user: User, service: RefreshTokenService) -> None:
    token = (await service.issue(user.id)).token

    await service.revoke(token)
    await service.revoke(token)


def test_hash_token_is_stable_and_not_reversible_looking() -> None:
    assert hash_token("abc") == hash_token("abc")
    assert hash_token("abc") != hash_token("abd")
    assert "abc" not in hash_token("abc")
    assert len(hash_token("abc")) == 64
