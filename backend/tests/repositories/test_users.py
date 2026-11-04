import pytest

from pulse.domain.errors import DuplicateUserError
from pulse.domain.users import Role
from pulse.repositories.database import Session
from pulse.repositories.users import UserRepository


@pytest.fixture
def repo(session: Session) -> UserRepository:
    return UserRepository(session)


async def test_create_assigns_increasing_ids(repo: UserRepository) -> None:
    users = [
        await repo.create(
            email=f"{n}@example.com", password_hash="h", role=Role.MANAGER
        )
        for n in range(3)
    ]

    ids = [u.id for u in users]
    assert ids == sorted(set(ids))


async def test_get_returns_user_or_none(repo: UserRepository) -> None:
    user = await repo.create(
        email="a@example.com", password_hash="h", role=Role.MANAGER
    )

    assert await repo.get(user.id) == user
    assert await repo.get(user.id + 1000) is None


async def test_find_by_email_is_exact(repo: UserRepository) -> None:
    user = await repo.create(
        email="a@example.com", password_hash="h", role=Role.MANAGER
    )

    assert await repo.find_by_email("a@example.com") == user
    assert await repo.find_by_email("A@example.com") is None  # нормализует сервис
    assert await repo.find_by_email("a@example.co") is None
    assert await repo.find_by_email("") is None


async def test_stored_user_keeps_hash_and_email(repo: UserRepository) -> None:
    created = await repo.create(
        email="a@example.com", password_hash="the-hash", role=Role.MANAGER
    )

    stored = await repo.get(created.id)
    assert stored is not None
    assert (stored.email, stored.password_hash) == ("a@example.com", "the-hash")


@pytest.mark.parametrize("role", list(Role))
async def test_create_keeps_the_given_role(repo: UserRepository, role: Role) -> None:
    created = await repo.create(email="a@x.io", password_hash="h", role=role)

    stored = await repo.get(created.id)
    assert stored is not None
    assert stored.role is role


async def test_create_requires_an_explicit_role(repo: UserRepository) -> None:
    # Как NOT NULL без DEFAULT в БД: роль задаёт сервис, а не умолчание хранилища.
    with pytest.raises(TypeError):
        await repo.create(email="a@x.io", password_hash="h")  # pyright: ignore[reportCallIssue]


async def test_duplicate_email_raises_domain_error(repo: UserRepository) -> None:
    await repo.create(email="a@example.com", password_hash="h", role=Role.MANAGER)

    with pytest.raises(DuplicateUserError):
        await repo.create(email="a@example.com", password_hash="h2", role=Role.VIEWER)


async def test_session_stays_usable_after_duplicate(repo: UserRepository) -> None:
    # Нарушение откатывает только SAVEPOINT вставки, а не всю транзакцию.
    first = await repo.create(
        email="a@example.com", password_hash="h", role=Role.MANAGER
    )
    with pytest.raises(DuplicateUserError):
        await repo.create(email="a@example.com", password_hash="h", role=Role.MANAGER)

    assert await repo.get(first.id) == first
    assert (
        await repo.create(email="b@example.com", password_hash="h", role=Role.MANAGER)
    ).email == "b@example.com"


async def test_database_rejects_unnormalized_email(repo: UserRepository) -> None:
    # Страховка схемы на случай, если сервис забудет normalize_email (CHECK в БД).
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError, match="users_email_normalized"):
        await repo.create(email="A@Example.com", password_hash="h", role=Role.MANAGER)
