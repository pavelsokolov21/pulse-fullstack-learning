from collections.abc import Callable, Iterator
from typing import Any

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pulse.api.deps import get_current_user
from pulse.api.main import app, create_app
from pulse.api.settings import ApiSettings
from pulse.domain.users import Role, User

SignIn = Callable[..., dict[str, str]]
URL = "/demo/concurrency"


@pytest.fixture
def client(admin_db: psycopg.Connection[tuple[object, ...]]) -> Iterator[TestClient]:
    # Демо работает в собственных сессиях и коммитит сразу, поэтому владелец монитора
    # должен существовать в базе заранее (подмена входа по умолчанию создаёт его
    # лишь внутри транзакции запроса, которая ещё не закоммичена).
    row = admin_db.execute(
        "INSERT INTO users (email, password_hash, role)"
        " VALUES ('demo@example.com', 'x', 'manager') RETURNING id"
    ).fetchone()
    assert row is not None
    user = User(
        id=int(row[0]),  # pyright: ignore[reportArgumentType]
        email="demo@example.com",
        password_hash="x",
        role=Role.MANAGER,
    )

    async def committed_user() -> User:
        return user

    app.dependency_overrides[get_current_user] = committed_user
    with TestClient(app) as test_client:
        yield test_client


@pytest.mark.parametrize(
    ("strategy", "final", "lost"),
    [("unsafe", 4, True), ("lock", 5, False), ("version", 5, False)],
)
def test_run_reports_what_happened(
    client: TestClient, strategy: str, final: int, lost: bool
) -> None:
    response = client.post(URL, json={"strategy": strategy, "hold_ms": 100})

    assert response.status_code == 200
    body = response.json()
    assert body["strategy"] == strategy
    assert (body["initial"], body["expected"], body["final"]) == (3, 5, final)
    assert body["lost_update"] is lost
    assert {e["kind"] for e in body["events"]} >= {"start", "read", "write", "commit"}
    assert sorted(c["call"] for c in body["calls"]) == [1, 2]


def test_default_hold_is_used(client: TestClient) -> None:
    response = client.post(URL, json={"strategy": "lock"})

    assert response.status_code == 200
    assert response.json()["hold_ms"] == 400


@pytest.mark.parametrize("hold_ms", [0, 99, 2001, -5])
def test_hold_outside_the_range_is_rejected(client: TestClient, hold_ms: int) -> None:
    response = client.post(URL, json={"strategy": "lock", "hold_ms": hold_ms})

    assert response.status_code == 422


def test_unknown_strategy_is_rejected(client: TestClient) -> None:
    assert client.post(URL, json={"strategy": "magic"}).status_code == 422


def test_anonymous_gets_401(auth_client: TestClient) -> None:
    assert auth_client.post(URL, json={"strategy": "lock"}).status_code == 401


def test_viewer_gets_403(auth_client: TestClient, sign_in: SignIn) -> None:
    headers = sign_in("viewer@example.com", Role.VIEWER)

    response = auth_client.post(URL, json={"strategy": "lock"}, headers=headers)

    assert response.status_code == 403


def test_manager_can_run_it(auth_client: TestClient, sign_in: SignIn) -> None:
    headers = sign_in("manager@example.com", Role.MANAGER)

    response = auth_client.post(
        URL, json={"strategy": "lock", "hold_ms": 100}, headers=headers
    )

    assert response.status_code == 200
    assert response.json()["final"] == 5


def test_prod_has_no_demo_endpoint(logged_in: Callable[[FastAPI], FastAPI]) -> None:
    prod = logged_in(create_app(ApiSettings(**dict[str, Any](environment="prod"))))
    with TestClient(prod) as client:
        assert client.post(URL, json={"strategy": "lock"}).status_code == 404
        assert URL not in client.get("/openapi.json").json()["paths"]
