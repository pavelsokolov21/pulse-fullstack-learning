from datetime import UTC, datetime, timedelta

import pytest

from pulse.domain.errors import (
    InvalidRefreshTokenError,
    RefreshTokenError,
    RefreshTokenExpiredError,
)
from pulse.repositories.refresh_tokens import RefreshTokenRepository
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
def repo() -> RefreshTokenRepository:
    return RefreshTokenRepository()


@pytest.fixture
def service(repo: RefreshTokenRepository, clock: Clock) -> RefreshTokenService:
    return RefreshTokenService(repo, TTL, clock)


# --- issue -----------------------------------------------------------------


def test_issue_returns_a_random_token_and_lifetime(
    service: RefreshTokenService,
) -> None:
    issued = service.issue(1)

    assert issued.expires_in == 7 * 24 * 3600
    assert len(issued.token) >= 43  # 32 байта в base64url


def test_issued_tokens_are_unique(service: RefreshTokenService) -> None:
    tokens = {service.issue(1).token for _ in range(50)}

    assert len(tokens) == 50


def test_only_the_hash_is_stored(
    service: RefreshTokenService, repo: RefreshTokenRepository
) -> None:
    issued = service.issue(1)

    record = repo.find_by_hash(hash_token(issued.token))
    assert record is not None
    assert record.token_hash != issued.token
    assert issued.token not in record.token_hash
    assert repo.find_by_hash(issued.token) is None


def test_issue_starts_a_new_family_each_time(
    service: RefreshTokenService, repo: RefreshTokenRepository
) -> None:
    first, second = service.issue(1), service.issue(1)

    a = repo.find_by_hash(hash_token(first.token))
    b = repo.find_by_hash(hash_token(second.token))
    assert a is not None
    assert b is not None
    assert a.family_id != b.family_id


def test_expiry_is_ttl_after_issue(
    service: RefreshTokenService, repo: RefreshTokenRepository
) -> None:
    issued = service.issue(1)

    record = repo.find_by_hash(hash_token(issued.token))
    assert record is not None
    assert record.expires_at == NOW + TTL


# --- rotate ----------------------------------------------------------------


def test_rotate_returns_user_and_a_different_token(
    service: RefreshTokenService,
) -> None:
    old = service.issue(5)

    user_id, new = service.rotate(old.token)

    assert user_id == 5
    assert new.token != old.token


def test_rotate_keeps_the_family_and_revokes_the_old_token(
    service: RefreshTokenService, repo: RefreshTokenRepository
) -> None:
    old = service.issue(1)
    _, new = service.rotate(old.token)

    old_record = repo.find_by_hash(hash_token(old.token))
    new_record = repo.find_by_hash(hash_token(new.token))
    assert old_record is not None
    assert new_record is not None
    assert old_record.revoked_at == NOW
    assert new_record.revoked_at is None
    assert old_record.family_id == new_record.family_id


def test_new_token_gets_a_fresh_lifetime(
    service: RefreshTokenService, repo: RefreshTokenRepository, clock: Clock
) -> None:
    old = service.issue(1)
    clock.now += timedelta(days=3)

    _, new = service.rotate(old.token)

    record = repo.find_by_hash(hash_token(new.token))
    assert record is not None
    assert record.expires_at == clock.now + TTL


def test_rotation_chain_keeps_working(service: RefreshTokenService) -> None:
    token = service.issue(1).token

    for _ in range(5):
        _, new = service.rotate(token)
        token = new.token

    assert service.rotate(token)[0] == 1


def test_unknown_token_is_rejected(service: RefreshTokenService) -> None:
    with pytest.raises(InvalidRefreshTokenError):
        service.rotate("never-issued")


@pytest.mark.parametrize("garbage", ["", " ", "a" * 10_000, "ы"])
def test_garbage_is_rejected(service: RefreshTokenService, garbage: str) -> None:
    with pytest.raises(InvalidRefreshTokenError):
        service.rotate(garbage)


# --- повторное использование ------------------------------------------------


def test_reusing_an_old_token_is_rejected(service: RefreshTokenService) -> None:
    old = service.issue(1)
    service.rotate(old.token)

    with pytest.raises(InvalidRefreshTokenError):
        service.rotate(old.token)


def test_reuse_revokes_the_whole_chain(
    service: RefreshTokenService, repo: RefreshTokenRepository
) -> None:
    first = service.issue(1)
    _, second = service.rotate(first.token)
    _, third = service.rotate(second.token)

    with pytest.raises(InvalidRefreshTokenError):
        service.rotate(first.token)  # вор предъявил самый первый токен

    # Законный владелец с актуальным токеном тоже вынужден войти заново.
    with pytest.raises(InvalidRefreshTokenError):
        service.rotate(third.token)
    family = repo.list_family(
        repo.find_by_hash(hash_token(first.token)).family_id  # pyright: ignore[reportOptionalMemberAccess]
    )
    assert len(family) == 3
    assert all(t.revoked_at is not None for t in family)


def test_reuse_does_not_touch_other_sessions(service: RefreshTokenService) -> None:
    stolen = service.issue(1)
    other_device = service.issue(1)
    service.rotate(stolen.token)

    with pytest.raises(InvalidRefreshTokenError):
        service.rotate(stolen.token)

    assert service.rotate(other_device.token)[0] == 1


def test_reuse_is_reported_as_a_generic_invalid_token(
    service: RefreshTokenService,
) -> None:
    old = service.issue(1)
    service.rotate(old.token)

    with pytest.raises(InvalidRefreshTokenError) as reused:
        service.rotate(old.token)
    with pytest.raises(InvalidRefreshTokenError) as unknown:
        service.rotate("never-issued")

    assert reused.value.code == unknown.value.code
    assert str(reused.value) == str(unknown.value)


# --- срок ------------------------------------------------------------------


def test_token_is_valid_until_the_last_second(
    service: RefreshTokenService, clock: Clock
) -> None:
    token = service.issue(1).token
    clock.now = NOW + TTL - timedelta(seconds=1)

    assert service.rotate(token)[0] == 1


@pytest.mark.parametrize("extra", [0, 1, 86400])
def test_expired_token_is_rejected(
    service: RefreshTokenService, clock: Clock, extra: int
) -> None:
    token = service.issue(1).token
    clock.now = NOW + TTL + timedelta(seconds=extra)

    with pytest.raises(RefreshTokenExpiredError):
        service.rotate(token)


def test_expired_error_is_a_refresh_token_error() -> None:
    assert issubclass(RefreshTokenExpiredError, RefreshTokenError)
    assert issubclass(InvalidRefreshTokenError, RefreshTokenError)


def test_rotation_of_expired_token_does_not_issue_a_new_one(
    service: RefreshTokenService, repo: RefreshTokenRepository, clock: Clock
) -> None:
    token = service.issue(1).token
    clock.now += TTL

    with pytest.raises(RefreshTokenExpiredError):
        service.rotate(token)

    family = repo.list_family(
        repo.find_by_hash(hash_token(token)).family_id  # pyright: ignore[reportOptionalMemberAccess]
    )
    assert len(family) == 1


# --- revoke ----------------------------------------------------------------


def test_revoke_ends_the_session(service: RefreshTokenService) -> None:
    token = service.issue(1).token

    service.revoke(token)

    with pytest.raises(InvalidRefreshTokenError):
        service.rotate(token)


def test_revoke_kills_the_current_token_of_the_chain(
    service: RefreshTokenService,
) -> None:
    first = service.issue(1)
    _, current = service.rotate(first.token)

    service.revoke(first.token)  # logout со старым токеном всё равно закрывает сессию

    with pytest.raises(InvalidRefreshTokenError):
        service.rotate(current.token)


def test_revoke_leaves_other_sessions_alone(service: RefreshTokenService) -> None:
    mine, other = service.issue(1), service.issue(1)

    service.revoke(mine.token)

    assert service.rotate(other.token)[0] == 1


def test_revoke_unknown_token_is_a_noop(service: RefreshTokenService) -> None:
    service.revoke("never-issued")


def test_revoke_twice_is_fine(service: RefreshTokenService) -> None:
    token = service.issue(1).token

    service.revoke(token)
    service.revoke(token)


def test_hash_token_is_stable_and_not_reversible_looking() -> None:
    assert hash_token("abc") == hash_token("abc")
    assert hash_token("abc") != hash_token("abd")
    assert "abc" not in hash_token("abc")
    assert len(hash_token("abc")) == 64
