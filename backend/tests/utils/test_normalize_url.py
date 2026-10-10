import pytest

from pulse.utils.normalize_url import normalize_url


@pytest.mark.parametrize(
    ("url", "error"),
    [
        pytest.param("", "пуст", id="empty"),
        pytest.param("   ", "пуст", id="blank"),
        pytest.param("https://", "Пустой хост", id="scheme-only"),
        pytest.param("http:///path", "Пустой хост", id="no-host"),
        pytest.param("https://:8080", "Пустой хост", id="port-without-host"),
        pytest.param("ftp://example.com", "протокол", id="unsupported-scheme"),
        # Без «://» строка считается хостом, поэтому «alert(1)» разбирается как порт.
        pytest.param("javascript:alert(1)", "порт", id="javascript-as-port"),
        pytest.param("http://example.com:abc", "порт", id="port-not-a-number"),
        pytest.param("example.com:99999", "порт", id="port-out-of-range"),
        pytest.param("example com", "пробел", id="space-in-host"),
        pytest.param("exa\tmple.com", "пробел", id="tab-in-host"),
        pytest.param("http://user:pass@example.com", "Логин", id="credentials"),
    ],
)
def test_invalid_url(url: str, error: str) -> None:
    with pytest.raises(ValueError, match=error):
        normalize_url(url)


VALID_URLS = [
    pytest.param("Example.com", "https://example.com", id="adds-https-lowercases-host"),
    pytest.param(
        "HTTPS://Example.COM/Path#top", "https://example.com/Path", id="drops-fragment"
    ),
    pytest.param("  example.com  ", "https://example.com", id="trims-spaces"),
    pytest.param("http://example.com", "http://example.com", id="keeps-http"),
    pytest.param(
        "example.com/a/b?x=1&y=2", "https://example.com/a/b?x=1&y=2", id="keeps-query"
    ),
    pytest.param(
        "example.com/Path?Q=Mixed#frag",
        "https://example.com/Path?Q=Mixed",
        id="keeps-path-and-query-case",
    ),
    pytest.param(
        "example.com:8080/health", "https://example.com:8080/health", id="keeps-port"
    ),
    # Регрессия: «://» внутри query или fragment не означает наличие схемы.
    pytest.param(
        "example.com/redirect?to=http://other.com",
        "https://example.com/redirect?to=http://other.com",
        id="scheme-inside-query",
    ),
    pytest.param(
        "example.com/#http://x", "https://example.com", id="scheme-inside-fragment"
    ),
    # Регрессия: без пути query раньше целиком приводился к нижнему регистру.
    pytest.param(
        "Example.com?Q=Mixed", "https://example.com?Q=Mixed", id="query-without-path"
    ),
    pytest.param(
        "example.com:8080?Q=Mixed",
        "https://example.com:8080?Q=Mixed",
        id="port-and-query-without-path",
    ),
    pytest.param("example.com/", "https://example.com", id="trailing-slash-after-host"),
    pytest.param("http://example.com:80", "http://example.com", id="default-http-port"),
    pytest.param(
        "https://example.com:443/x", "https://example.com/x", id="default-https-port"
    ),
    pytest.param(
        "http://example.com:443",
        "http://example.com:443",
        id="port-default-for-other-scheme",
    ),
    pytest.param(
        "http://EXAMPLE.com:8080", "http://example.com:8080", id="http-with-port"
    ),
    pytest.param("[::1]:8080/health", "https://[::1]:8080/health", id="ipv6-with-port"),
]


@pytest.mark.parametrize(("url", "expected"), VALID_URLS)
def test_valid_url(url: str, expected: str) -> None:
    assert normalize_url(url) == expected


@pytest.mark.parametrize(("url", "expected"), VALID_URLS)
def test_normalization_is_idempotent(url: str, expected: str) -> None:
    assert normalize_url(expected) == expected
