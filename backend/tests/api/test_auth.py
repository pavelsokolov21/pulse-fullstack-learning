from typing import Any, Protocol

import pytest
from fastapi.testclient import TestClient

from pulse.domain.users import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH

PASSWORD = "correct horse battery"


class Clock(Protocol):
    """Часы из фикстуры `clock` (tests/api/conftest.py): время двигает тест."""

    def advance(self, **kwargs: float) -> None: ...


def register(
    client: TestClient, email: str = "alice@example.com", password: str = PASSWORD
) -> Any:
    return client.post("/auth/register", json={"email": email, "password": password})


def login(
    client: TestClient, email: str = "alice@example.com", password: str = PASSWORD
) -> Any:
    # OAuth2 password flow: форма, а не JSON; email лежит в поле username.
    return client.post("/auth/login", data={"username": email, "password": password})


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def token_of(client: TestClient) -> str:
    register(client)
    return login(client).json()["access_token"]


# --- POST /auth/register ---------------------------------------------------


def test_register_returns_201_and_the_user_without_hash(
    auth_client: TestClient,
) -> None:
    response = register(auth_client)

    assert response.status_code == 201
    assert response.json() == {"id": 1, "email": "alice@example.com"}


def test_register_response_has_no_password_material(auth_client: TestClient) -> None:
    response = register(auth_client)

    assert "argon2" not in response.text
    assert PASSWORD not in response.text
    assert "password" not in response.text


def test_register_normalizes_email(auth_client: TestClient) -> None:
    response = register(auth_client, email="  Alice@Example.COM ")

    assert response.json()["email"] == "alice@example.com"


@pytest.mark.parametrize(
    "again", ["alice@example.com", "ALICE@EXAMPLE.COM", "  alice@example.com\t"]
)
def test_register_duplicate_returns_409(auth_client: TestClient, again: str) -> None:
    register(auth_client)

    response = register(auth_client, email=again)

    assert response.status_code == 409
    assert response.json() == {
        "code": "user_already_exists",
        "message": "Пользователь с таким email уже существует",
    }


def test_users_get_increasing_ids(auth_client: TestClient) -> None:
    ids = [
        register(auth_client, email=f"u{n}@example.com").json()["id"] for n in range(3)
    ]

    assert ids == [1, 2, 3]


@pytest.mark.parametrize("length", [MIN_PASSWORD_LENGTH, MAX_PASSWORD_LENGTH])
def test_register_accepts_password_length_bounds(
    auth_client: TestClient, length: int
) -> None:
    assert register(auth_client, password="p" * length).status_code == 201


@pytest.mark.parametrize(
    "length", [0, MIN_PASSWORD_LENGTH - 1, MAX_PASSWORD_LENGTH + 1, 5000]
)
def test_register_rejects_password_length_outside_bounds(
    auth_client: TestClient, length: int
) -> None:
    response = register(auth_client, password="p" * length)

    assert response.status_code == 422
    assert response.json()["errors"][0]["loc"] == ["body", "password"]


@pytest.mark.parametrize(
    "email",
    ["", "not-an-email", "a@", "@example.com", "a b@example.com", "a@@example.com"],
)
def test_register_rejects_invalid_email(auth_client: TestClient, email: str) -> None:
    response = register(auth_client, email=email)

    assert response.status_code == 422
    assert response.json()["errors"][0]["loc"] == ["body", "email"]


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"email": "a@example.com"},
        {"password": PASSWORD},
        {"email": "a@example.com", "password": PASSWORD, "id": 5},
        {"email": "a@example.com", "password": PASSWORD, "is_admin": True},
        {"email": None, "password": PASSWORD},
        {"email": "a@example.com", "password": 12345678},
    ],
)
def test_register_rejects_malformed_body(
    auth_client: TestClient, body: dict[str, Any]
) -> None:
    assert auth_client.post("/auth/register", json=body).status_code == 422


def test_register_validation_error_does_not_echo_the_password(
    auth_client: TestClient,
) -> None:
    secret = "short-pw"[: MIN_PASSWORD_LENGTH - 1]

    response = register(auth_client, email="broken", password=secret)

    assert response.status_code == 422
    assert secret not in response.text


def test_failed_registration_is_not_stored(auth_client: TestClient) -> None:
    register(auth_client, password="short")

    assert login(auth_client, password="short").status_code == 401
    assert register(auth_client).json()["id"] == 1


# --- POST /auth/login ------------------------------------------------------


def test_login_returns_bearer_token(auth_client: TestClient) -> None:
    register(auth_client)

    response = login(auth_client)

    body = response.json()
    assert response.status_code == 200
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 900
    assert body["access_token"].count(".") == 2


def test_login_response_is_not_cacheable(auth_client: TestClient) -> None:
    register(auth_client)

    response = login(auth_client)

    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"


@pytest.mark.parametrize("email", ["ALICE@example.com", " alice@example.com "])
def test_login_normalizes_email(auth_client: TestClient, email: str) -> None:
    register(auth_client)

    assert login(auth_client, email=email).status_code == 200


def test_wrong_password_returns_401_with_www_authenticate(
    auth_client: TestClient,
) -> None:
    register(auth_client)

    response = login(auth_client, password="wrong password")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json() == {
        "code": "invalid_credentials",
        "message": "Неверный email или пароль",
    }


def test_unknown_email_is_indistinguishable_from_wrong_password(
    auth_client: TestClient,
) -> None:
    register(auth_client)

    unknown = login(auth_client, email="nobody@example.com")
    wrong = login(auth_client, password="wrong password")

    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json() == wrong.json()
    assert unknown.headers["www-authenticate"] == wrong.headers["www-authenticate"]


def test_too_long_password_is_just_invalid_credentials(
    auth_client: TestClient,
) -> None:
    register(auth_client)

    response = login(auth_client, password="x" * 5000)

    assert response.status_code == 401
    assert response.json()["code"] == "invalid_credentials"


@pytest.mark.parametrize(
    "data", [{}, {"username": "a@example.com"}, {"password": PASSWORD}]
)
def test_login_requires_both_form_fields(
    auth_client: TestClient, data: dict[str, str]
) -> None:
    response = auth_client.post("/auth/login", data=data)

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


def test_login_does_not_accept_json_body(auth_client: TestClient) -> None:
    register(auth_client)

    response = auth_client.post(
        "/auth/login", json={"username": "alice@example.com", "password": PASSWORD}
    )

    assert response.status_code == 422


def test_login_is_repeatable_and_gives_working_tokens(auth_client: TestClient) -> None:
    register(auth_client)

    for _ in range(2):
        token = login(auth_client).json()["access_token"]
        assert auth_client.get("/auth/me", headers=bearer(token)).status_code == 200


# --- GET /auth/me ----------------------------------------------------------


def test_me_returns_the_current_user(auth_client: TestClient) -> None:
    token = token_of(auth_client)

    response = auth_client.get("/auth/me", headers=bearer(token))

    assert response.status_code == 200
    assert response.json() == {"id": 1, "email": "alice@example.com"}


def test_me_distinguishes_users(auth_client: TestClient) -> None:
    register(auth_client, email="a@example.com")
    register(auth_client, email="b@example.com")
    token_b = login(auth_client, email="b@example.com").json()["access_token"]

    assert (
        auth_client.get("/auth/me", headers=bearer(token_b)).json()["email"]
        == "b@example.com"
    )


def test_me_without_token_returns_401(auth_client: TestClient) -> None:
    response = auth_client.get("/auth/me")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json() == {
        "code": "not_authenticated",
        "message": "Требуется авторизация",
    }


@pytest.mark.parametrize(
    "header",
    ["", "Bearer", "Bearer ", "Basic dXNlcjpwYXNz", "Token abc", "abc"],
)
def test_me_with_wrong_authorization_header_returns_401(
    auth_client: TestClient, header: str
) -> None:
    response = auth_client.get("/auth/me", headers={"Authorization": header})

    assert response.status_code == 401
    assert response.json()["code"] == "not_authenticated"
    assert response.headers["www-authenticate"] == "Bearer"


def test_me_with_garbage_token_returns_invalid_token(auth_client: TestClient) -> None:
    response = auth_client.get("/auth/me", headers=bearer("not.a.token"))

    assert response.status_code == 401
    assert response.json()["code"] == "invalid_token"
    assert response.headers["www-authenticate"] == "Bearer"


def test_me_with_forged_token_returns_invalid_token(auth_client: TestClient) -> None:
    token = token_of(auth_client)
    header, payload, signature = token.split(".")
    forged = f"{header}.{payload}.{signature[::-1]}"

    response = auth_client.get("/auth/me", headers=bearer(forged))

    assert response.status_code == 401
    assert response.json()["code"] == "invalid_token"


def test_me_with_expired_token_returns_token_expired(
    auth_client: TestClient, clock: Clock
) -> None:
    token = token_of(auth_client)
    clock.advance(minutes=15)

    response = auth_client.get("/auth/me", headers=bearer(token))

    assert response.status_code == 401
    assert response.json() == {
        "code": "token_expired",
        "message": "Срок действия токена истёк",
    }
    assert response.headers["www-authenticate"] == "Bearer"


def test_token_works_right_before_expiry(auth_client: TestClient, clock: Clock) -> None:
    token = token_of(auth_client)
    clock.advance(minutes=14, seconds=59)

    assert auth_client.get("/auth/me", headers=bearer(token)).status_code == 200


def test_new_login_after_expiry_gives_a_working_token(
    auth_client: TestClient, clock: Clock
) -> None:
    old = token_of(auth_client)
    clock.advance(minutes=20)
    fresh = login(auth_client).json()["access_token"]

    assert auth_client.get("/auth/me", headers=bearer(old)).status_code == 401
    assert auth_client.get("/auth/me", headers=bearer(fresh)).status_code == 200


# --- OpenAPI ---------------------------------------------------------------


def test_openapi_describes_the_password_flow(auth_client: TestClient) -> None:
    spec = auth_client.get("/openapi.json").json()

    scheme = next(iter(spec["components"]["securitySchemes"].values()))
    assert scheme["type"] == "oauth2"
    assert scheme["flows"]["password"]["tokenUrl"] == "auth/login"
    assert "409" in spec["paths"]["/auth/register"]["post"]["responses"]
    assert "401" in spec["paths"]["/auth/login"]["post"]["responses"]
    assert "401" in spec["paths"]["/auth/me"]["get"]["responses"]


def test_openapi_does_not_expose_the_password_hash(auth_client: TestClient) -> None:
    text = auth_client.get("/openapi.json").text

    assert "password_hash" not in text
