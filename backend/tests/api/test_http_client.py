import httpx

from pulse.api.http_client import create_http_client


def cookie_server() -> tuple[httpx.MockTransport, list[str]]:
    """Сайт, который выдаёт cookie и запоминает, что клиент прислал обратно."""
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.headers.get("cookie", ""))
        return httpx.Response(200, headers={"set-cookie": "sid=abc; Path=/"})

    return httpx.MockTransport(handler), sent


async def test_cookies_from_one_response_are_not_sent_back() -> None:
    transport, sent = cookie_server()

    async with create_http_client(transport) as client:
        await client.get("https://a.test/")
        await client.get("https://a.test/")

    assert sent == ["", ""]


async def test_cookies_are_not_shared_between_sites() -> None:
    transport, sent = cookie_server()

    async with create_http_client(transport) as client:
        await client.get("https://a.test/")
        await client.get("https://b.test/")

    assert sent == ["", ""]


async def test_cookie_jar_stays_empty() -> None:
    transport, _ = cookie_server()

    async with create_http_client(transport) as client:
        await client.get("https://a.test/")

    assert len(client.cookies.jar) == 0


async def test_default_httpx_client_does_send_cookies_back() -> None:
    # Контрольный тест: без политики cookie уезжали бы дальше. Он доказывает,
    # что тесты выше проверяют именно политику, а не особенность мока.
    transport, sent = cookie_server()

    async with httpx.AsyncClient(transport=transport) as client:
        await client.get("https://a.test/")
        await client.get("https://a.test/")

    assert sent == ["", "sid=abc"]


async def test_client_is_open_and_independent() -> None:
    first, second = create_http_client(), create_http_client()
    try:
        assert first is not second
        assert not first.is_closed
    finally:
        await first.aclose()
        await second.aclose()
