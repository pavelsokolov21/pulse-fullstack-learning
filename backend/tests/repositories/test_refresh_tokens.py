from datetime import UTC, datetime, timedelta

from pulse.repositories.refresh_tokens import RefreshTokenRepository

NOW = datetime(2026, 1, 1, tzinfo=UTC)
LATER = NOW + timedelta(hours=1)


def add(
    repo: RefreshTokenRepository,
    token_hash: str = "h1",
    family: str = "f1",
    user: int = 1,
):
    return repo.add(
        user_id=user,
        family_id=family,
        token_hash=token_hash,
        expires_at=NOW + timedelta(days=7),
    )


def test_add_assigns_increasing_ids_and_starts_unrevoked() -> None:
    repo = RefreshTokenRepository()

    first, second = add(repo, "a"), add(repo, "b")

    assert (first.id, second.id) == (1, 2)
    assert first.revoked_at is None


def test_find_by_hash_returns_the_record_or_none() -> None:
    repo = RefreshTokenRepository()
    token = add(repo, "abc")

    assert repo.find_by_hash("abc") == token
    assert repo.find_by_hash("nope") is None


def test_revoke_marks_only_that_token() -> None:
    repo = RefreshTokenRepository()
    first, second = add(repo, "a"), add(repo, "b")

    repo.revoke(first.id, LATER)

    found_first, found_second = repo.find_by_hash("a"), repo.find_by_hash("b")
    assert found_first is not None
    assert found_first.revoked_at == LATER
    assert found_second == second


def test_revoke_does_not_overwrite_the_first_revocation_time() -> None:
    repo = RefreshTokenRepository()
    token = add(repo)

    repo.revoke(token.id, NOW)
    repo.revoke(token.id, LATER)

    found = repo.find_by_hash("h1")
    assert found is not None
    assert found.revoked_at == NOW


def test_revoke_family_revokes_every_token_of_the_chain() -> None:
    repo = RefreshTokenRepository()
    add(repo, "a", "f1")
    add(repo, "b", "f1")
    other = add(repo, "c", "f2")

    repo.revoke_family("f1", LATER)

    assert all(t.revoked_at == LATER for t in repo.list_family("f1"))
    assert repo.find_by_hash("c") == other


def test_revoke_family_keeps_earlier_revocation_times() -> None:
    repo = RefreshTokenRepository()
    first = add(repo, "a", "f1")
    add(repo, "b", "f1")
    repo.revoke(first.id, NOW)

    repo.revoke_family("f1", LATER)

    times = {t.token_hash: t.revoked_at for t in repo.list_family("f1")}
    assert times == {"a": NOW, "b": LATER}


def test_revoke_unknown_family_is_a_noop() -> None:
    RefreshTokenRepository().revoke_family("nope", NOW)


def test_repr_does_not_contain_the_hash() -> None:
    token = add(RefreshTokenRepository(), "secret-hash-value")

    assert "secret-hash-value" not in repr(token)
