import pytest

from pulse.domain.users import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH, User


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
