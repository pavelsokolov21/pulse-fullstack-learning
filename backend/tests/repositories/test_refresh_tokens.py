import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from pulse.domain.refresh_tokens import RefreshToken
from pulse.domain.users import Role, User
from pulse.repositories.database import Session
from pulse.repositories.refresh_tokens import RefreshTokenRepository
from pulse.repositories.users import UserRepository

NOW = datetime(2026, 1, 1, tzinfo=UTC)
LATER = NOW + timedelta(hours=1)

FAMILY_1 = uuid.UUID(int=1).hex
FAMILY_2 = uuid.UUID(int=2).hex


def sha(label: str) -> str:
    """Хеш нужного формата (64 hex): CHECK в схеме отвергает произвольные строки."""
    return label.encode().hex().ljust(64, "0")[:64]


@pytest.fixture
def repo(session: Session) -> RefreshTokenRepository:
    return RefreshTokenRepository(session)


async def add(
    repo: RefreshTokenRepository, user: User, label: str = "a", family: str = FAMILY_1
) -> RefreshToken:
    return await repo.add(
        user_id=user.id,
        family_id=family,
        token_hash=sha(label),
        expires_at=NOW + timedelta(days=7),
    )


async def test_add_assigns_increasing_ids_and_starts_unrevoked(
    repo: RefreshTokenRepository, owner: User
) -> None:
    first, second = await add(repo, owner, "a"), await add(repo, owner, "b")

    assert first.id < second.id
    assert first.revoked_at is None


async def test_add_keeps_all_given_fields(
    repo: RefreshTokenRepository, owner: User
) -> None:
    token = await add(repo, owner, "a", FAMILY_2)

    assert token == RefreshToken(
        token.id, owner.id, FAMILY_2, sha("a"), NOW + timedelta(days=7), None
    )


async def test_find_by_hash_returns_the_record_or_none(
    repo: RefreshTokenRepository, owner: User
) -> None:
    token = await add(repo, owner, "abc")

    assert await repo.find_by_hash(sha("abc")) == token
    assert await repo.find_by_hash(sha("nope")) is None


async def test_revoke_marks_only_that_token(
    repo: RefreshTokenRepository, owner: User
) -> None:
    first, second = await add(repo, owner, "a"), await add(repo, owner, "b")

    assert await repo.revoke(first.id, LATER) is True

    found_first = await repo.find_by_hash(sha("a"))
    assert found_first is not None
    assert found_first.revoked_at == LATER
    assert await repo.find_by_hash(sha("b")) == second


async def test_revoke_does_not_overwrite_the_first_revocation_time(
    repo: RefreshTokenRepository, owner: User
) -> None:
    token = await add(repo, owner)

    first_result = await repo.revoke(token.id, NOW)
    second_result = await repo.revoke(token.id, LATER)

    found = await repo.find_by_hash(sha("a"))
    assert found is not None
    assert found.revoked_at == NOW
    # Второй отзыв сообщает, что токен уже был отозван: по этому признаку сервис
    # отличает проигравшего в гонке двух обновлений.
    assert (first_result, second_result) == (True, False)


async def test_revoke_unknown_token_reports_false(
    repo: RefreshTokenRepository,
) -> None:
    assert await repo.revoke(424242, NOW) is False


async def test_revoke_family_revokes_every_token_of_the_chain(
    repo: RefreshTokenRepository, owner: User
) -> None:
    await add(repo, owner, "a", FAMILY_1)
    await add(repo, owner, "b", FAMILY_1)
    other = await add(repo, owner, "c", FAMILY_2)

    await repo.revoke_family(FAMILY_1, LATER)

    family = await repo.list_family(FAMILY_1)
    assert len(family) == 2
    assert all(t.revoked_at == LATER for t in family)
    assert await repo.find_by_hash(sha("c")) == other


async def test_revoke_family_keeps_earlier_revocation_times(
    repo: RefreshTokenRepository, owner: User
) -> None:
    first = await add(repo, owner, "a", FAMILY_1)
    await add(repo, owner, "b", FAMILY_1)
    await repo.revoke(first.id, NOW)

    await repo.revoke_family(FAMILY_1, LATER)

    times = {t.token_hash: t.revoked_at for t in await repo.list_family(FAMILY_1)}
    assert times == {sha("a"): NOW, sha("b"): LATER}


async def test_revoke_unknown_family_is_a_noop(
    repo: RefreshTokenRepository, owner: User
) -> None:
    token = await add(repo, owner, "a", FAMILY_1)

    await repo.revoke_family(FAMILY_2, NOW)

    assert await repo.find_by_hash(sha("a")) == token


async def test_list_family_is_in_issue_order_and_limited_to_the_family(
    repo: RefreshTokenRepository, owner: User
) -> None:
    first = await add(repo, owner, "a", FAMILY_1)
    await add(repo, owner, "z", FAMILY_2)
    second = await add(repo, owner, "b", FAMILY_1)

    assert await repo.list_family(FAMILY_1) == [first, second]


async def test_reads_after_revocation_see_fresh_data(
    repo: RefreshTokenRepository, owner: User
) -> None:
    # Токен уже загружен в сессию, потом отозван UPDATE-ом: повторное чтение
    # обязано показать отзыв, а не копию из памяти сессии.
    token = await add(repo, owner)
    assert (await repo.find_by_hash(sha("a"))) == token

    await repo.revoke_family(FAMILY_1, LATER)

    refreshed = await repo.find_by_hash(sha("a"))
    assert refreshed is not None
    assert refreshed.revoked_at == LATER
    assert (await repo.list_family(FAMILY_1))[0].revoked_at == LATER


async def test_deleting_the_user_deletes_their_tokens(
    session: Session, repo: RefreshTokenRepository
) -> None:
    user = await UserRepository(session).create(
        email="gone@example.com", password_hash="h", role=Role.MANAGER
    )
    await add(repo, user)

    await session.execute(text("DELETE FROM users WHERE id = :id"), {"id": user.id})

    assert await repo.find_by_hash(sha("a")) is None


async def test_token_hash_must_be_unique(
    repo: RefreshTokenRepository, owner: User
) -> None:
    await add(repo, owner, "a", FAMILY_1)

    with pytest.raises(IntegrityError, match="refresh_tokens_token_hash_key"):
        await add(repo, owner, "a", FAMILY_2)


async def test_database_rejects_a_raw_token_instead_of_its_hash(
    repo: RefreshTokenRepository, owner: User
) -> None:
    with pytest.raises(IntegrityError, match="refresh_tokens_token_hash_sha256"):
        await repo.add(
            user_id=owner.id,
            family_id=FAMILY_1,
            token_hash="raw-token-not-a-hash",
            expires_at=NOW,
        )


def test_repr_does_not_contain_the_hash() -> None:
    token = RefreshToken(1, 1, FAMILY_1, "secret-hash-value", NOW)

    assert "secret-hash-value" not in repr(token)
