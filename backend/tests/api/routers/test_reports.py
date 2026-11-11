"""GET /monitors/{id}/report: кеш Redis, X-Cache и актуальность после проверки."""

from collections.abc import Callable
from typing import Any

import httpx
import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pulse.api.deps import get_http_client
from pulse.domain.users import Role

Db = psycopg.Connection[tuple[object, ...]]
SignIn = Callable[..., dict[str, str]]


@pytest.fixture(autouse=True)
def fake_web(auth_app: FastAPI) -> None:
    web = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200))
    )
    auth_app.dependency_overrides[get_http_client] = lambda: web


def monitor(client: TestClient, headers: dict[str, str], url: str = "a.test") -> int:
    response = client.post(
        "/monitors", json={"url": url, "interval": "5m"}, headers=headers
    )
    assert response.status_code == 201
    return int(response.json()["id"])


def report(client: TestClient, headers: dict[str, str], monitor_id: int, **params: Any):
    return client.get(f"/monitors/{monitor_id}/report", params=params, headers=headers)


def run_check(client: TestClient, headers: dict[str, str], monitor_id: int) -> None:
    assert (
        client.post(f"/monitors/{monitor_id}/checks", headers=headers).status_code
        == 201
    )


@pytest.fixture
def headers(auth_client: TestClient, sign_in: SignIn) -> dict[str, str]:
    return sign_in("alice@example.com", Role.MANAGER)


def test_empty_monitor_has_a_report_with_nulls(
    auth_client: TestClient, headers: dict[str, str]
) -> None:
    monitor_id = monitor(auth_client, headers)

    response = report(auth_client, headers, monitor_id)

    assert response.status_code == 200
    body = response.json()
    assert (body["total"], body["ok"]) == (0, 0)
    assert body["uptime_percent"] is None
    assert body["p95_latency_ms"] is None
    assert body["window"] == "7d"  # окно по умолчанию
    assert body["monitor_id"] == monitor_id


def test_second_read_is_served_from_the_cache(
    auth_client: TestClient, headers: dict[str, str]
) -> None:
    monitor_id = monitor(auth_client, headers)
    run_check(auth_client, headers, monitor_id)

    first = report(auth_client, headers, monitor_id)
    second = report(auth_client, headers, monitor_id)

    assert (first.headers["x-cache"], second.headers["x-cache"]) == ("MISS", "HIT")
    assert first.json() == second.json()


def test_report_is_fresh_right_after_a_new_check(
    auth_client: TestClient, headers: dict[str, str]
) -> None:
    # Запиши проверку и сразу прочитай отчёт: кеш не должен отдать прошлое.
    monitor_id = monitor(auth_client, headers)
    run_check(auth_client, headers, monitor_id)
    assert report(auth_client, headers, monitor_id).json()["total"] == 1
    assert report(auth_client, headers, monitor_id).headers["x-cache"] == "HIT"

    run_check(auth_client, headers, monitor_id)
    after = report(auth_client, headers, monitor_id)

    assert after.headers["x-cache"] == "MISS"
    assert after.json()["total"] == 2
    assert after.json()["uptime_percent"] == 100.0


def test_every_window_is_available_and_cached_separately(
    auth_client: TestClient, headers: dict[str, str]
) -> None:
    monitor_id = monitor(auth_client, headers)
    run_check(auth_client, headers, monitor_id)

    for window in ("24h", "7d", "30d"):
        first = report(auth_client, headers, monitor_id, window=window)
        assert first.status_code == 200
        assert first.json()["window"] == window
        assert first.headers["x-cache"] == "MISS"  # у каждого окна свой ключ


@pytest.mark.parametrize("window", ["1y", "", "24", "7D", "all"])
def test_unknown_window_is_rejected(
    auth_client: TestClient, headers: dict[str, str], window: str
) -> None:
    monitor_id = monitor(auth_client, headers)

    assert report(auth_client, headers, monitor_id, window=window).status_code == 422


def test_unknown_monitor_is_404(
    auth_client: TestClient, headers: dict[str, str]
) -> None:
    assert report(auth_client, headers, 999).status_code == 404


def test_cached_report_is_not_shown_to_another_user(
    auth_client: TestClient, headers: dict[str, str], sign_in: SignIn
) -> None:
    monitor_id = monitor(auth_client, headers)
    run_check(auth_client, headers, monitor_id)
    assert report(auth_client, headers, monitor_id).status_code == 200  # лежит в кеше
    bob = sign_in("bob@example.com", Role.MANAGER)

    response = report(auth_client, bob, monitor_id)

    assert (
        response.status_code == 404
    )  # не 403: чужой монитор неотличим от отсутствующего
    assert "x-cache" not in response.headers


def test_viewer_can_read_reports_of_their_own_monitors_only(
    auth_client: TestClient, headers: dict[str, str], sign_in: SignIn
) -> None:
    monitor_id = monitor(auth_client, headers)
    viewer = sign_in("viewer@example.com", Role.VIEWER)

    assert report(auth_client, viewer, monitor_id).status_code == 404


def test_report_requires_authentication(auth_client: TestClient) -> None:
    assert auth_client.get("/monitors/1/report").status_code == 401


def test_deleted_monitor_has_no_report(
    auth_client: TestClient, headers: dict[str, str]
) -> None:
    monitor_id = monitor(auth_client, headers)
    report(auth_client, headers, monitor_id)  # кеш заполнен

    auth_client.delete(f"/monitors/{monitor_id}", headers=headers)

    assert report(auth_client, headers, monitor_id).status_code == 404
