import pytest

from pulse.domain.users import Role
from pulse.repositories.users import UserRepository


def test_create_assigns_increasing_ids_from_one() -> None:
    repo = UserRepository()

    ids = [
        repo.create(email=f"{n}@example.com", password_hash="h", role=Role.MANAGER).id
        for n in range(3)
    ]

    assert ids == [1, 2, 3]


def test_get_returns_user_or_none() -> None:
    repo = UserRepository()
    user = repo.create(email="a@example.com", password_hash="h", role=Role.MANAGER)

    assert repo.get(user.id) == user
    assert repo.get(99) is None


def test_find_by_email_is_exact() -> None:
    repo = UserRepository()
    user = repo.create(email="a@example.com", password_hash="h", role=Role.MANAGER)

    assert repo.find_by_email("a@example.com") == user
    assert repo.find_by_email("A@example.com") is None  # нормализует сервис
    assert repo.find_by_email("a@example.co") is None
    assert repo.find_by_email("") is None


def test_stored_user_keeps_hash_and_email() -> None:
    user = UserRepository().create(
        email="a@example.com", password_hash="the-hash", role=Role.MANAGER
    )

    assert (user.email, user.password_hash) == ("a@example.com", "the-hash")


def test_repositories_do_not_share_state() -> None:
    first, second = UserRepository(), UserRepository()
    first.create(email="a@example.com", password_hash="h", role=Role.MANAGER)

    assert second.find_by_email("a@example.com") is None


def test_create_keeps_the_given_role() -> None:
    repo = UserRepository()

    assert (
        repo.create(email="a@x.io", password_hash="h", role=Role.VIEWER).role
        is Role.VIEWER
    )
    assert (
        repo.create(email="b@x.io", password_hash="h", role=Role.ADMIN).role
        is Role.ADMIN
    )


def test_create_requires_an_explicit_role() -> None:
    # Как NOT NULL без DEFAULT в БД: роль задаёт сервис, а не умолчание хранилища.
    repo = UserRepository()

    with pytest.raises(TypeError):
        repo.create(email="a@x.io", password_hash="h")  # pyright: ignore[reportCallIssue]
