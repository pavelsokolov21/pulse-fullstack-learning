from http.cookiejar import DefaultCookiePolicy

import httpx


def create_http_client(
    transport: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncClient:
    """Общий клиент для проверок сайтов.

    Cookie отключены: клиент один на всё приложение, и без этого cookie одного
    проверяемого сайта уезжали бы в последующие запросы (в том числе к другим
    сайтам), искажая результат проверки.
    """
    client = httpx.AsyncClient(transport=transport)
    client.cookies.jar.set_policy(DefaultCookiePolicy(allowed_domains=[]))
    return client
