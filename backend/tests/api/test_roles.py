from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pulse.api.deps import get_http_client
from pulse.domain.users import Role

SignIn = Callable[..., dict[str, str]]

BODY = {"url": "a.test", "interval": "5m"}


@pytest.fixture(autouse=True)
def fake_web(auth_app: FastAPI) -> None:
    web = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200))
    )
    auth_app.dependency_overrides[get_http_client] = lambda: web


def create(
    client: TestClient, headers: dict[str, str], url: str = "a.test"
) -> dict[str, Any]:
    response = client.post(
        "/monitors", json={"url": url, "interval": "5m"}, headers=headers
    )
    assert response.status_code == 201
    return response.json()


# --- регистрация выдаёт безопасную роль ------------------------------------


def test_registration_creates_a_manager(auth_client: TestClient) -> None:
    response = auth_client.post(
        "/auth/register",
        json={"email": "new@example.com", "password": "correct horse battery"},
    )

    assert response.json()["role"] == "manager"


@pytest.mark.parametrize("role", ["admin", "viewer", "root"])
def test_role_cannot_be_chosen_at_registration(
    auth_client: TestClient, role: str
) -> None:
    response = auth_client.post(
        "/auth/register",
        json={
            "email": "new@example.com",
            "password": "correct horse battery",
            "role": role,
        },
    )

    assert response.status_code == 422
    assert (
        auth_client.post(
            "/auth/login",
            data={"username": "new@example.com", "password": "correct horse battery"},
        ).status_code
        == 401
    )


def test_me_reports_the_role(auth_client: TestClient, sign_in: SignIn) -> None:
    headers = sign_in("admin@example.com", Role.ADMIN)

    assert auth_client.get("/auth/me", headers=headers).json()["role"] == "admin"


# --- 403: роль не разрешает действие ---------------------------------------

WRITE_OPERATIONS: list[tuple[str, str, dict[str, Any] | None]] = [
    ("POST", "/monitors", BODY),
    ("PATCH", "/monitors/1", {"timeout": 5}),
    ("DELETE", "/monitors/1", None),
    ("POST", "/monitors/1/checks", None),
]
READ_OPERATIONS: list[tuple[str, str]] = [
    ("GET", "/monitors"),
    ("GET", "/monitors/1"),
    ("GET", "/monitors/1/checks"),
]


@pytest.mark.parametrize(("method", "path", "body"), WRITE_OPERATIONS)
def test_viewer_cannot_write(
    auth_client: TestClient,
    sign_in: SignIn,
    method: str,
    path: str,
    body: dict[str, Any] | None,
) -> None:
    headers = sign_in("viewer@example.com", Role.VIEWER)

    response = auth_client.request(method, path, json=body, headers=headers)

    assert response.status_code == 403
    assert response.json() == {
        "code": "forbidden",
        "message": "Недостаточно прав для этого действия",
    }


@pytest.mark.parametrize(("method", "path"), READ_OPERATIONS)
def test_viewer_can_read(
    auth_client: TestClient, sign_in: SignIn, method: str, path: str
) -> None:
    headers = sign_in("viewer@example.com", Role.VIEWER)

    response = auth_client.request(method, path, headers=headers)

    assert response.status_code != 403


def test_forbidden_write_changes_nothing(
    auth_client: TestClient, sign_in: SignIn
) -> None:
    viewer = sign_in("viewer@example.com", Role.VIEWER)

    auth_client.post("/monitors", json=BODY, headers=viewer)

    assert auth_client.get("/monitors", headers=viewer).json()["items"] == []


def test_403_is_not_an_authentication_error(
    auth_client: TestClient, sign_in: SignIn
) -> None:
    headers = sign_in("viewer@example.com", Role.VIEWER)

    response = auth_client.post("/monitors", json=BODY, headers=headers)

    # Пользователь известен: не просим войти заново.
    assert response.status_code == 403
    assert "www-authenticate" not in response.headers


def test_anonymous_gets_401_not_403(auth_client: TestClient) -> None:
    response = auth_client.post("/monitors", json=BODY)

    assert response.status_code == 401


def test_role_is_checked_before_the_body(
    auth_client: TestClient, sign_in: SignIn
) -> None:
    headers = sign_in("viewer@example.com", Role.VIEWER)

    response = auth_client.post("/monitors", json={"nonsense": 1}, headers=headers)

    assert response.status_code == 403


@pytest.mark.parametrize("role", [Role.ADMIN, Role.MANAGER])
def test_admin_and_manager_can_write(
    auth_client: TestClient, sign_in: SignIn, role: Role
) -> None:
    headers = sign_in(f"{role}@example.com", role)

    assert auth_client.post("/monitors", json=BODY, headers=headers).status_code == 201


def test_openapi_documents_403_on_write_routes(auth_client: TestClient) -> None:
    paths = auth_client.get("/openapi.json").json()["paths"]

    assert "403" in paths["/monitors"]["post"]["responses"]
    assert "403" in paths["/monitors/{monitor_id}"]["patch"]["responses"]
    assert "403" in paths["/monitors/{monitor_id}"]["delete"]["responses"]
    assert "403" in paths["/monitors/{monitor_id}/checks"]["post"]["responses"]
    assert "403" not in paths["/monitors"]["get"]["responses"]


# --- владение: IDOR --------------------------------------------------------


def test_owner_is_the_creator_and_cannot_be_set_in_the_request(
    auth_client: TestClient, sign_in: SignIn
) -> None:
    alice = sign_in("alice@example.com")
    sign_in("bob@example.com")

    ignored = auth_client.post("/monitors", json={**BODY, "owner_id": 2}, headers=alice)
    created = create(auth_client, alice)

    assert ignored.status_code == 422
    assert created["owner_id"] == 1


def test_list_shows_only_own_monitors(auth_client: TestClient, sign_in: SignIn) -> None:
    alice, bob = sign_in("alice@example.com"), sign_in("bob@example.com")
    create(auth_client, alice, "a.test")
    create(auth_client, bob, "b.test")

    urls = [
        m["url"] for m in auth_client.get("/monitors", headers=alice).json()["items"]
    ]

    assert urls == ["https://a.test"]


@pytest.mark.parametrize(
    ("method", "path_suffix", "body"),
    [
        ("GET", "", None),
        ("PATCH", "", {"timeout": 1}),
        ("DELETE", "", None),
        ("GET", "/checks", None),
        ("POST", "/checks", None),
    ],
)
def test_foreign_monitor_returns_404(
    auth_client: TestClient,
    sign_in: SignIn,
    method: str,
    path_suffix: str,
    body: dict[str, Any] | None,
) -> None:
    alice, bob = sign_in("alice@example.com"), sign_in("bob@example.com")
    monitor = create(auth_client, alice)

    response = auth_client.request(
        method, f"/monitors/{monitor['id']}{path_suffix}", json=body, headers=bob
    )

    assert response.status_code == 404
    assert response.json()["code"] == "monitor_not_found"


@pytest.mark.parametrize(
    ("method", "suffix", "body"),
    [
        ("GET", "", None),
        ("PATCH", "", {"timeout": 1}),
        ("DELETE", "", None),
        ("GET", "/checks", None),
        ("POST", "/checks", None),
    ],
)
def test_foreign_monitor_is_indistinguishable_from_a_missing_one(
    auth_client: TestClient,
    sign_in: SignIn,
    method: str,
    suffix: str,
    body: dict[str, Any] | None,
) -> None:
    alice, bob = sign_in("alice@example.com"), sign_in("bob@example.com")
    foreign = create(auth_client, alice)["id"]

    existing = auth_client.request(
        method, f"/monitors/{foreign}{suffix}", json=body, headers=bob
    )
    missing = auth_client.request(
        method, f"/monitors/9999{suffix}", json=body, headers=bob
    )

    assert existing.status_code == missing.status_code == 404
    assert existing.json()["code"] == missing.json()["code"]
    assert existing.text.replace(str(foreign), "N") == missing.text.replace("9999", "N")


def test_foreign_monitor_stays_untouched(
    auth_client: TestClient, sign_in: SignIn
) -> None:
    alice, bob = sign_in("alice@example.com"), sign_in("bob@example.com")
    monitor = create(auth_client, alice)

    auth_client.patch(f"/monitors/{monitor['id']}", json={"timeout": 1}, headers=bob)
    auth_client.delete(f"/monitors/{monitor['id']}", headers=bob)

    assert (
        auth_client.get(f"/monitors/{monitor['id']}", headers=alice).json() == monitor
    )


def test_foreign_check_history_is_not_readable(
    auth_client: TestClient, sign_in: SignIn
) -> None:
    alice, bob = sign_in("alice@example.com"), sign_in("bob@example.com")
    monitor = create(auth_client, alice)
    auth_client.post(f"/monitors/{monitor['id']}/checks", headers=alice)

    response = auth_client.get(f"/monitors/{monitor['id']}/checks", headers=bob)

    assert response.status_code == 404
    assert "status_code" not in response.text


def test_foreign_monitor_is_not_requested_over_the_network(
    auth_app: FastAPI, auth_client: TestClient, sign_in: SignIn
) -> None:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    web = httpx.AsyncClient(transport=httpx.MockTransport(record))
    auth_app.dependency_overrides[get_http_client] = lambda: web
    alice, bob = sign_in("alice@example.com"), sign_in("bob@example.com")
    monitor = create(auth_client, alice)

    auth_client.post(f"/monitors/{monitor['id']}/checks", headers=bob)

    assert seen == []


def test_same_url_for_two_owners_is_not_a_conflict(
    auth_client: TestClient, sign_in: SignIn
) -> None:
    alice, bob = sign_in("alice@example.com"), sign_in("bob@example.com")
    create(auth_client, alice, "same.test")

    response = auth_client.post(
        "/monitors", json={"url": "same.test", "interval": "5m"}, headers=bob
    )

    assert response.status_code == 201


def test_duplicate_for_the_same_owner_is_a_conflict(
    auth_client: TestClient, sign_in: SignIn
) -> None:
    alice = sign_in("alice@example.com")
    create(auth_client, alice, "same.test")

    response = auth_client.post(
        "/monitors", json={"url": "same.test", "interval": "5m"}, headers=alice
    )

    assert response.status_code == 409


def test_409_never_mentions_a_foreign_monitor(
    auth_client: TestClient, sign_in: SignIn
) -> None:
    alice, bob = sign_in("alice@example.com"), sign_in("bob@example.com")
    create(auth_client, alice, "same.test")
    own = create(auth_client, bob, "other.test")

    response = auth_client.patch(
        f"/monitors/{own['id']}", json={"url": "same.test"}, headers=bob
    )

    # У Боба нет монитора с этим URL, так что конфликта нет: чужой id не всплывает.
    assert response.status_code == 200
    assert response.json()["url"] == "https://same.test"


# --- администратор ---------------------------------------------------------


def test_admin_sees_all_monitors(auth_client: TestClient, sign_in: SignIn) -> None:
    alice, bob = sign_in("alice@example.com"), sign_in("bob@example.com")
    admin = sign_in("admin@example.com", Role.ADMIN)
    create(auth_client, alice, "a.test")
    create(auth_client, bob, "b.test")

    urls = [
        m["url"] for m in auth_client.get("/monitors", headers=admin).json()["items"]
    ]

    assert urls == ["https://a.test", "https://b.test"]


def test_admin_can_read_change_check_and_delete_foreign_monitor(
    auth_client: TestClient, sign_in: SignIn
) -> None:
    alice = sign_in("alice@example.com")
    admin = sign_in("admin@example.com", Role.ADMIN)
    monitor = create(auth_client, alice)
    url = f"/monitors/{monitor['id']}"

    assert auth_client.get(url, headers=admin).status_code == 200
    patched = auth_client.patch(url, json={"timeout": 3}, headers=admin)
    assert patched.json()["timeout"] == 3.0
    assert patched.json()["owner_id"] == monitor["owner_id"]
    assert auth_client.post(f"{url}/checks", headers=admin).status_code == 201
    assert auth_client.get(f"{url}/checks", headers=admin).status_code == 200
    assert auth_client.delete(url, headers=admin).status_code == 204
    assert auth_client.get(url, headers=alice).status_code == 404


def test_deleted_monitor_takes_its_checks_with_it(
    auth_client: TestClient, sign_in: SignIn
) -> None:
    alice = sign_in("alice@example.com")
    monitor = create(auth_client, alice)
    auth_client.post(f"/monitors/{monitor['id']}/checks", headers=alice)

    auth_client.delete(f"/monitors/{monitor['id']}", headers=alice)

    assert (
        auth_client.get(f"/monitors/{monitor['id']}/checks", headers=alice).status_code
        == 404
    )


def test_admin_list_shows_each_owners_email_and_the_full_total(
    auth_client: TestClient, sign_in: SignIn
) -> None:
    alice, bob = sign_in("alice@example.com"), sign_in("bob@example.com")
    admin = sign_in("admin@example.com", Role.ADMIN)
    create(auth_client, alice, "a.test")
    create(auth_client, bob, "b.test")

    page = auth_client.get("/monitors", headers=admin).json()

    assert page["total"] == 2
    assert [(m["url"], m["owner_email"]) for m in page["items"]] == [
        ("https://a.test", "alice@example.com"),
        ("https://b.test", "bob@example.com"),
    ]


def test_manager_total_counts_only_own_monitors(
    auth_client: TestClient, sign_in: SignIn
) -> None:
    alice, bob = sign_in("alice@example.com"), sign_in("bob@example.com")
    create(auth_client, alice, "a.test")
    create(auth_client, bob, "b.test")
    create(auth_client, bob, "c.test")

    page = auth_client.get("/monitors", headers=alice).json()

    # Чужие мониторы не видны ни в items, ни в total: иначе число выдало бы их.
    assert page["total"] == 1
    assert [m["owner_email"] for m in page["items"]] == ["alice@example.com"]
