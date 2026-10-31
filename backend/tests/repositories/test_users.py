from pulse.domain.users import Role
from pulse.repositories.users import UserRepository


def test_create_assigns_increasing_ids_from_one() -> None:
    repo = UserRepository()

    ids = [
        repo.create(email=f"{n}@example.com", password_hash="h").id for n in range(3)
    ]

    assert ids == [1, 2, 3]


def test_get_returns_user_or_none() -> None:
    repo = UserRepository()
    user = repo.create(email="a@example.com", password_hash="h")

    assert repo.get(user.id) == user
    assert repo.get(99) is None


def test_find_by_email_is_exact() -> None:
    repo = UserRepository()
    user = repo.create(email="a@example.com", password_hash="h")

    assert repo.find_by_email("a@example.com") == user
    assert repo.find_by_email("A@example.com") is None  # нормализует сервис
    assert repo.find_by_email("a@example.co") is None
    assert repo.find_by_email("") is None


def test_stored_user_keeps_hash_and_email() -> None:
    user = UserRepository().create(email="a@example.com", password_hash="the-hash")

    assert (user.email, user.password_hash) == ("a@example.com", "the-hash")


def test_repositories_do_not_share_state() -> None:
    first, second = UserRepository(), UserRepository()
    first.create(email="a@example.com", password_hash="h")

    assert second.find_by_email("a@example.com") is None


def test_create_defaults_to_manager_and_keeps_given_role() -> None:
    repo = UserRepository()

    assert repo.create(email="a@example.com", password_hash="h").role is Role.MANAGER
    assert (
        repo.create(email="b@example.com", password_hash="h", role=Role.ADMIN).role
        is Role.ADMIN
    )
