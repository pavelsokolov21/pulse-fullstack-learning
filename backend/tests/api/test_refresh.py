from typing import Any, Protocol

import pytest
from fastapi.testclient import TestClient

from pulse.api.main import create_app
from pulse.api.settings import ApiSettings

PASSWORD = "correct horse battery"
COOKIE = "refresh_token"


class Clock(Protocol):
    def advance(self, **kwargs: float) -> None: ...


def register(client: TestClient) -> None:
    client.post(
        "/auth/register", json={"email": "alice@example.com", "password": PASSWORD}
    )


def login(client: TestClient) -> Any:
    return client.post(
        "/auth/login", data={"username": "alice@example.com", "password": PASSWORD}
    )


def start_session(client: TestClient) -> tuple[str, str]:
    """Регистрирует, входит; возвращает (access, refresh-cookie)."""
    register(client)
    response = login(client)
    refresh = client.cookies.get(COOKIE)
    assert refresh is not None
    return response.json()["access_token"], refresh


def use_cookie(client: TestClient, value: str) -> None:
    client.cookies.clear()
    client.cookies.set(COOKIE, value, domain="testserver.local", path="/auth")


def refresh(client: TestClient) -> Any:
    return client.post("/auth/refresh")


def assert_rejected_as_invalid(client: TestClient) -> None:
    """401 именно из-за недействительного токена, а не из-за отсутствия cookie."""
    response = refresh(client)

    assert response.status_code == 401
    assert response.json()["code"] == "invalid_refresh_token"


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- login кладёт refresh в cookie ----------------------------------------


def test_login_returns_access_in_body_and_refresh_in_cookie(
    auth_client: TestClient,
) -> None:
    register(auth_client)

    response = login(auth_client)

    assert response.json()["access_token"]
    assert "refresh_token" not in response.json()
    assert auth_client.cookies.get(COOKIE)


def test_refresh_token_never_appears_in_the_body(auth_client: TestClient) -> None:
    register(auth_client)

    response = login(auth_client)
    cookie = auth_client.cookies.get(COOKIE)

    assert cookie is not None
    assert cookie not in response.text


def test_refresh_cookie_attributes(auth_client: TestClient) -> None:
    register(auth_client)

    header = login(auth_client).headers["set-cookie"].lower()

    assert f"{COOKIE}=" in header
    assert "httponly" in header
    assert "path=/auth" in header
    assert "samesite=strict" in header
    assert f"max-age={7 * 24 * 3600}" in header
    assert "secure" not in header.replace("samesite", "")  # http в dev


def test_refresh_ttl_comes_from_settings() -> None:
    # Настоящий сервис без подмены в фикстуре: срок берётся из ApiSettings.
    overrides: dict[str, Any] = {"refresh_token_ttl_days": 3}
    app = create_app(ApiSettings(**overrides))

    with TestClient(app) as client:
        register(client)
        header = login(client).headers["set-cookie"].lower()

    assert f"max-age={3 * 24 * 3600}" in header


def test_refresh_cookie_path_comes_from_settings() -> None:
    # За прокси Next.js API живёт под /api, и cookie должна уходить на /api/auth.
    overrides: dict[str, Any] = {"refresh_cookie_path": "/api/auth"}
    app = create_app(ApiSettings(**overrides))

    with TestClient(app) as client:
        register(client)
        issued = login(client).headers["set-cookie"].lower()
        client.cookies.set(COOKIE, "garbage", domain="testserver.local", path="/auth")
        rejected = client.post("/auth/refresh").headers["set-cookie"].lower()
        cleared = client.post("/auth/logout").headers["set-cookie"].lower()

    assert "path=/api/auth" in issued
    # Сброс должен совпадать по пути, иначе браузер не удалит cookie.
    assert "path=/api/auth" in rejected
    assert "path=/api/auth" in cleared


def test_refresh_cookie_is_secure_in_prod() -> None:
    app = create_app(ApiSettings(environment="prod"))  # pyright: ignore[reportCallIssue]

    with TestClient(app) as client:
        client.post(
            "/auth/register",
            json={"email": "alice@example.com", "password": PASSWORD},
        )
        response = login(client)

    assert "secure" in response.headers["set-cookie"].lower().replace("samesite", "")


def test_access_token_and_cookie_are_different_values(auth_client: TestClient) -> None:
    access, refresh_token = start_session(auth_client)

    assert access != refresh_token
    assert access.count(".") == 2  # JWT
    assert "." not in refresh_token  # непрозрачная строка


# --- POST /auth/refresh ----------------------------------------------------


def test_refresh_returns_a_new_working_access_token(auth_client: TestClient) -> None:
    start_session(auth_client)

    response = refresh(auth_client)

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 900
    assert (
        auth_client.get("/auth/me", headers=bearer(body["access_token"])).status_code
        == 200
    )


def test_refresh_rotates_the_cookie(auth_client: TestClient) -> None:
    _, first = start_session(auth_client)

    response = refresh(auth_client)

    second = auth_client.cookies.get(COOKIE)
    assert second is not None
    assert second != first
    assert f"{COOKIE}=" in response.headers["set-cookie"]


def test_refresh_response_is_not_cacheable(auth_client: TestClient) -> None:
    start_session(auth_client)

    response = refresh(auth_client)

    assert response.headers["cache-control"] == "no-store"


def test_refresh_can_be_repeated_along_the_chain(auth_client: TestClient) -> None:
    start_session(auth_client)

    statuses = [refresh(auth_client).status_code for _ in range(5)]

    assert statuses == [200] * 5


def test_refresh_without_cookie_returns_401(auth_client: TestClient) -> None:
    response = refresh(auth_client)

    assert response.status_code == 401
    assert response.json()["code"] == "not_authenticated"
    assert response.headers["www-authenticate"] == "Bearer"


def test_refresh_with_unknown_cookie_returns_401_and_clears_it(
    auth_client: TestClient,
) -> None:
    use_cookie(auth_client, "never-issued")

    response = refresh(auth_client)

    assert response.status_code == 401
    assert response.json()["code"] == "invalid_refresh_token"
    assert "max-age=0" in response.headers["set-cookie"].lower()


def test_refresh_ignores_a_token_in_the_body_or_query(auth_client: TestClient) -> None:
    _, refresh_token = start_session(auth_client)
    auth_client.cookies.clear()

    by_body = auth_client.post("/auth/refresh", json={"refresh_token": refresh_token})
    by_query = auth_client.post(f"/auth/refresh?refresh_token={refresh_token}")

    assert by_body.status_code == by_query.status_code == 401


def test_access_token_is_not_accepted_as_refresh(auth_client: TestClient) -> None:
    access, _ = start_session(auth_client)
    use_cookie(auth_client, access)

    assert refresh(auth_client).status_code == 401


def test_refresh_token_is_not_accepted_as_access(auth_client: TestClient) -> None:
    _, refresh_token = start_session(auth_client)

    response = auth_client.get("/auth/me", headers=bearer(refresh_token))

    assert response.status_code == 401
    assert response.json()["code"] == "invalid_token"


# --- повторное использование -----------------------------------------------


def test_reusing_an_old_refresh_token_is_rejected(auth_client: TestClient) -> None:
    _, old = start_session(auth_client)
    refresh(auth_client)  # ротация: old отозван

    use_cookie(auth_client, old)
    response = refresh(auth_client)

    assert response.status_code == 401
    assert response.json()["code"] == "invalid_refresh_token"


def test_reuse_revokes_the_whole_chain_including_the_current_token(
    auth_client: TestClient,
) -> None:
    _, stolen = start_session(auth_client)
    refresh(auth_client)
    current = auth_client.cookies.get(COOKIE)
    assert current is not None

    use_cookie(auth_client, stolen)  # вор предъявляет старый токен
    assert_rejected_as_invalid(auth_client)

    use_cookie(auth_client, current)  # теперь и законный владелец отозван
    assert_rejected_as_invalid(auth_client)


def test_reuse_does_not_end_other_sessions(auth_client: TestClient) -> None:
    _, stolen = start_session(auth_client)
    refresh(auth_client)
    auth_client.cookies.clear()
    login(auth_client)  # второе устройство: своя цепочка
    other = auth_client.cookies.get(COOKIE)
    assert other is not None

    use_cookie(auth_client, stolen)
    refresh(auth_client)  # реузе → отзыв первой цепочки

    use_cookie(auth_client, other)
    assert refresh(auth_client).status_code == 200


def test_expired_refresh_token_returns_401(
    auth_client: TestClient, clock: Clock
) -> None:
    start_session(auth_client)
    clock.advance(days=7)

    response = refresh(auth_client)

    assert response.status_code == 401
    assert response.json() == {
        "code": "refresh_token_expired",
        "message": "Срок действия refresh-токена истёк",
    }
    assert response.headers["www-authenticate"] == "Bearer"


def test_refresh_works_right_before_expiry(
    auth_client: TestClient, clock: Clock
) -> None:
    start_session(auth_client)
    clock.advance(days=6, hours=23, minutes=59)

    assert refresh(auth_client).status_code == 200


def test_refresh_extends_the_session(auth_client: TestClient, clock: Clock) -> None:
    start_session(auth_client)
    clock.advance(days=6)
    assert refresh(auth_client).status_code == 200

    clock.advance(days=6)  # 12 дней с входа, но токен обновлялся 6 дней назад

    assert refresh(auth_client).status_code == 200


def test_access_token_expires_independently_and_refresh_recovers(
    auth_client: TestClient, clock: Clock
) -> None:
    access, _ = start_session(auth_client)
    clock.advance(minutes=20)
    assert auth_client.get("/auth/me", headers=bearer(access)).status_code == 401

    fresh = refresh(auth_client).json()["access_token"]

    assert auth_client.get("/auth/me", headers=bearer(fresh)).status_code == 200


# --- POST /auth/logout -----------------------------------------------------


def test_logout_returns_204_and_clears_the_cookie(auth_client: TestClient) -> None:
    start_session(auth_client)

    response = auth_client.post("/auth/logout")

    assert response.status_code == 204
    assert response.content == b""
    assert auth_client.cookies.get(COOKIE) is None
    header = response.headers["set-cookie"].lower()
    assert "max-age=0" in header
    assert "path=/auth" in header


def test_refresh_after_logout_is_rejected(auth_client: TestClient) -> None:
    _, token = start_session(auth_client)
    auth_client.post("/auth/logout")

    use_cookie(auth_client, token)

    assert_rejected_as_invalid(auth_client)


def test_logout_revokes_the_whole_chain(auth_client: TestClient) -> None:
    _, first = start_session(auth_client)
    refresh(auth_client)
    auth_client.post("/auth/logout")

    use_cookie(auth_client, first)

    assert_rejected_as_invalid(auth_client)


def test_logout_is_idempotent_and_needs_no_session(auth_client: TestClient) -> None:
    assert auth_client.post("/auth/logout").status_code == 204
    assert auth_client.post("/auth/logout").status_code == 204


def test_logout_with_garbage_cookie_still_succeeds(auth_client: TestClient) -> None:
    use_cookie(auth_client, "garbage")

    assert auth_client.post("/auth/logout").status_code == 204


def test_logout_of_one_session_keeps_another(auth_client: TestClient) -> None:
    start_session(auth_client)
    first = auth_client.cookies.get(COOKIE)
    assert first is not None
    auth_client.cookies.clear()
    login(auth_client)
    second = auth_client.cookies.get(COOKIE)
    assert second is not None

    use_cookie(auth_client, first)
    auth_client.post("/auth/logout")
    use_cookie(auth_client, second)

    assert refresh(auth_client).status_code == 200


def test_access_token_still_works_until_it_expires_after_logout(
    auth_client: TestClient, clock: Clock
) -> None:
    # Известное ограничение stateless-JWT: отозвать можно только refresh.
    access, _ = start_session(auth_client)
    auth_client.post("/auth/logout")
    assert auth_client.get("/auth/me", headers=bearer(access)).status_code == 200

    clock.advance(minutes=15)

    assert auth_client.get("/auth/me", headers=bearer(access)).status_code == 401


def test_openapi_lists_refresh_and_logout(auth_client: TestClient) -> None:
    paths = auth_client.get("/openapi.json").json()["paths"]

    assert "401" in paths["/auth/refresh"]["post"]["responses"]
    assert "204" in paths["/auth/logout"]["post"]["responses"]


@pytest.mark.parametrize("method", ["GET", "PUT", "DELETE"])
def test_refresh_only_accepts_post(auth_client: TestClient, method: str) -> None:
    assert auth_client.request(method, "/auth/refresh").status_code == 405
