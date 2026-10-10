import pytest

from pulse.utils.normalize_url import normalize_url


@pytest.mark.parametrize(
    "url",
    [
        "",
        "   ",
        "https://",
        "http:///path",
        "ftp://example.com",
        "javascript:alert(1)",
        "example com",
    ],
)
def test_invalid_url(url: str) -> None:
    with pytest.raises(ValueError):
        normalize_url(url)


@pytest.mark.parametrize(
    "url,expected",
    [
        ("Example.com", "https://example.com"),
        ("HTTPS://Example.COM/Path#top", "https://example.com/Path"),
        ("  example.com  ", "https://example.com"),
        ("http://example.com", "http://example.com"),
        ("example.com/a/b?x=1&y=2", "https://example.com/a/b?x=1&y=2"),
        ("example.com/Path?Q=Mixed#frag", "https://example.com/Path?Q=Mixed"),
        ("example.com:8080/health", "https://example.com:8080/health"),
    ],
)
def test_valid_url(url: str, expected: str) -> None:
    assert normalize_url(url) == expected
