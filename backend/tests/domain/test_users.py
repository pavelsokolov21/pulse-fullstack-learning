import pytest

from pulse.domain.users import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH, Role, User


def test_repr_does_not_contain_password_hash() -> None:
    user = User(1, "a@example.com", "$argon2id$secret-hash")

    assert "secret-hash" not in repr(user)
    assert "a@example.com" in repr(user)
    assert "secret-hash" not in f"{user}"


def test_user_is_immutable() -> None:
    user = User(1, "a@example.com", "h")

    with pytest.raises(AttributeError):
        user.email = "b@example.com"  # type: ignore[misc]


def test_password_length_limits() -> None:
    assert (MIN_PASSWORD_LENGTH, MAX_PASSWORD_LENGTH) == (8, 128)


def test_roles_are_strings_with_stable_values() -> None:
    assert [r.value for r in Role] == ["admin", "manager", "viewer"]
    assert Role("admin") is Role.ADMIN
    assert Role.ADMIN == "admin"


def test_unknown_role_is_rejected() -> None:
    with pytest.raises(ValueError, match="root"):
        Role("root")


def test_default_role_is_manager() -> None:
    assert User(1, "a@example.com", "h").role is Role.MANAGER


def test_repr_shows_role_but_still_hides_hash() -> None:
    user = User(1, "a@example.com", "secret-hash", Role.ADMIN)

    assert "admin" in repr(user)
    assert "secret-hash" not in repr(user)
