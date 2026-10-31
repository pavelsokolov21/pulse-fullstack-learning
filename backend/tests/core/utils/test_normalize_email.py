import pytest

from pulse.core.utils.normalize_email import normalize_email


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("alice@example.com", "alice@example.com"),
        ("  Alice@Example.COM  ", "alice@example.com"),
        ("\tBOB@EXAMPLE.COM\n", "bob@example.com"),
        ("", ""),
        ("   ", ""),
    ],
)
def test_email_is_stripped_and_lowercased(raw: str, expected: str) -> None:
    assert normalize_email(raw) == expected


@pytest.mark.parametrize("raw", ["  A@B.com ", "x@Y.org", "ПОЧТА@Пример.рф"])
def test_normalization_is_idempotent(raw: str) -> None:
    once = normalize_email(raw)

    assert normalize_email(once) == once


def test_inner_spaces_are_kept() -> None:
    # Это не валидация: некорректный адрес отвергает схема API, а не нормализация.
    assert normalize_email("a b@example.com") == "a b@example.com"
