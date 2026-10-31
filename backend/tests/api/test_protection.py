from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pulse.api.deps import get_http_client

PASSWORD = "correct horse battery"

# (метод, путь, тело) всех ручек, которые обязаны быть закрыты авторизацией.
PROTECTED: list[tuple[str, str, dict[str, Any] | None]] = [
    ("POST", "/monitors", {"url": "a.test", "interval": "5m"}),
    ("GET", "/monitors", None),
    ("GET", "/monitors/1", None),
    ("PATCH", "/monitors/1", {"timeout": 5}),
    ("DELETE", "/monitors/1", None),
    ("POST", "/monitors/1/checks", None),
    ("GET", "/monitors/1/checks", None),
    ("GET", "/auth/me", None),
]
IDS = [f"{method} {path}" for method, path, _ in PROTECTED]


def call(
    client: TestClient,
    method: str,
    path: str,
    body: dict[str, Any] | None,
    headers: dict[str, str] | None = None,
):
    return client.request(method, path, json=body, headers=headers)


def sign_up(client: TestClient) -> str:
    client.post("/auth/register", json={"email": "a@example.com", "password": PASSWORD})
    response = client.post(
        "/auth/login", data={"username": "a@example.com", "password": PASSWORD}
    )
    return response.json()["access_token"]


@pytest.mark.parametrize(("method", "path", "body"), PROTECTED, ids=IDS)
def test_route_requires_a_token(
    auth_client: TestClient, method: str, path: str, body: dict[str, Any] | None
) -> None:
    response = call(auth_client, method, path, body)

    assert response.status_code == 401
    assert response.json()["code"] == "not_authenticated"
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize(("method", "path", "body"), PROTECTED, ids=IDS)
def test_route_rejects_a_garbage_token(
    auth_client: TestClient, method: str, path: str, body: dict[str, Any] | None
) -> None:
    response = call(
        auth_client, method, path, body, {"Authorization": "Bearer garbage"}
    )

    assert response.status_code == 401
    assert response.json()["code"] == "invalid_token"


@pytest.mark.parametrize(("method", "path", "body"), PROTECTED, ids=IDS)
def test_route_rejects_an_expired_token(
    auth_client: TestClient,
    clock: Any,
    method: str,
    path: str,
    body: dict[str, Any] | None,
) -> None:
    token = sign_up(auth_client)
    clock.advance(minutes=15)

    response = call(
        auth_client, method, path, body, {"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 401
    assert response.json()["code"] == "token_expired"


@pytest.mark.parametrize(("method", "path", "body"), PROTECTED, ids=IDS)
def test_route_accepts_a_valid_token(
    auth_client: TestClient, method: str, path: str, body: dict[str, Any] | None
) -> None:
    token = sign_up(auth_client)

    response = call(
        auth_client, method, path, body, {"Authorization": f"Bearer {token}"}
    )

    # Не 401: проверка авторизации пройдена (дальше может быть 404, 201 и т. д.).
    assert response.status_code != 401


def test_auth_is_checked_before_the_body_is_validated(auth_client: TestClient) -> None:
    # Аноним не должен узнавать, какие поля у закрытой ручки.
    response = auth_client.post("/monitors", json={"nonsense": True})

    assert response.status_code == 401


def test_auth_is_checked_before_the_monitor_lookup(auth_client: TestClient) -> None:
    # Аноним не должен узнавать, какие id существуют.
    assert auth_client.get("/monitors/999").status_code == 401


def test_openapi_marks_every_monitor_and_check_route_as_secured(
    auth_app: FastAPI,
) -> None:
    # Страховка от забытой ручки: схема OpenAPI показывает требование авторизации
    # у каждой операции под /monitors. Новая ручка роутера получит его сама.
    operations = [
        (method, path)
        for path, item in auth_app.openapi()["paths"].items()
        if path.startswith("/monitors")
        for method in item
    ]
    unsecured = [
        (method, path)
        for method, path in operations
        if not auth_app.openapi()["paths"][path][method].get("security")
    ]

    assert len(operations) == 7
    assert unsecured == []


def test_public_auth_routes_are_not_marked_as_secured(auth_app: FastAPI) -> None:
    paths = auth_app.openapi()["paths"]

    assert "security" not in paths["/auth/register"]["post"]
    assert "security" not in paths["/auth/login"]["post"]
    assert paths["/auth/me"]["get"]["security"]


# --- что остаётся открытым -------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path", "body", "allowed"),
    [
        (
            "POST",
            "/auth/register",
            {"email": "x@example.com", "password": PASSWORD},
            201,
        ),
        ("POST", "/auth/login", None, 422),
        ("POST", "/auth/refresh", None, 401),
        ("POST", "/auth/logout", None, 204),
        ("GET", "/openapi.json", None, 200),
        ("GET", "/docs", None, 200),
        ("GET", "/no-such-route", None, 404),
    ],
)
def test_public_routes_need_no_token(
    auth_client: TestClient,
    method: str,
    path: str,
    body: dict[str, Any] | None,
    allowed: int,
) -> None:
    assert call(auth_client, method, path, body).status_code == allowed


# --- сквозной сценарий -----------------------------------------------------


def test_full_flow_register_login_use_monitors(auth_app: FastAPI) -> None:
    web = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200))
    )
    auth_app.dependency_overrides[get_http_client] = lambda: web

    with TestClient(auth_app) as client:
        headers = {"Authorization": f"Bearer {sign_up(client)}"}

        created = client.post(
            "/monitors", json={"url": "a.test", "interval": "5m"}, headers=headers
        )
        run = client.post(f"/monitors/{created.json()['id']}/checks", headers=headers)
        listed = client.get(f"/monitors/{created.json()['id']}/checks", headers=headers)

    assert created.status_code == 201
    assert run.status_code == 201
    assert [c["ok"] for c in listed.json()] == [True]


def test_tokens_do_not_carry_over_between_apps(auth_app: FastAPI) -> None:
    from pulse.api.main import create_app

    with TestClient(auth_app) as first:
        token = sign_up(first)

    other = create_app()
    with TestClient(other) as second:
        # В другом приложении нет этого пользователя, хотя подпись верна.
        response = second.get("/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
