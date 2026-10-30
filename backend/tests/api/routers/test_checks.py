from collections.abc import Callable, Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from pulse.api.deps import get_http_client
from pulse.api.main import app
from pulse.repositories.checks import MAX_CHECKS_PER_MONITOR

Handler = Callable[[httpx.Request], httpx.Response]


class Web:
    """Подмена внешней сети: запоминает запросы, ответ задаёт `handler`."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.handler: Handler = lambda request: httpx.Response(200)
        transport = httpx.MockTransport(self._handle)
        self.client = httpx.AsyncClient(transport=transport)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request)


@pytest.fixture
def web() -> Web:
    return Web()


@pytest.fixture
def client(web: Web) -> Iterator[TestClient]:
    app.dependency_overrides[get_http_client] = lambda: web.client
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()


def create_monitor(client: TestClient, url: str = "a.test", **extra: object) -> int:
    response = client.post("/monitors", json={"url": url, "interval": "5m", **extra})
    assert response.status_code == 201
    return response.json()["id"]


def run(client: TestClient, monitor_id: int):
    return client.post(f"/monitors/{monitor_id}/checks")


# --- POST /monitors/{id}/checks -------------------------------------------


def test_run_returns_201_with_the_check(client: TestClient) -> None:
    monitor_id = create_monitor(client)

    response = run(client, monitor_id)

    body = response.json()
    assert response.status_code == 201
    assert body["id"] == 1
    assert body["monitor_id"] == monitor_id
    assert body["url"] == "https://a.test"
    assert (body["status_code"], body["error"], body["ok"]) == (200, None, True)
    assert body["latency_ms"] >= 0


def test_run_returns_timezone_aware_utc_timestamp(client: TestClient) -> None:
    monitor_id = create_monitor(client)

    checked_at: str = run(client, monitor_id).json()["checked_at"]

    assert checked_at.endswith(("Z", "+00:00"))


def test_run_requests_the_monitor_url(client: TestClient, web: Web) -> None:
    monitor_id = create_monitor(client, "a.test/health")

    run(client, monitor_id)

    assert [str(r.url) for r in web.requests] == ["https://a.test/health"]


def test_server_error_is_a_failed_check_not_an_api_error(
    client: TestClient, web: Web
) -> None:
    web.handler = lambda request: httpx.Response(503)
    monitor_id = create_monitor(client)

    response = run(client, monitor_id)

    assert response.status_code == 201
    assert (response.json()["status_code"], response.json()["ok"]) == (503, False)


def test_unreachable_site_is_a_failed_check_not_an_api_error(
    client: TestClient, web: Web
) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    web.handler = refuse
    monitor_id = create_monitor(client)

    body = run(client, monitor_id).json()

    assert body["status_code"] is None
    assert body["ok"] is False
    assert body["error"] == "Network Error: ConnectError"


def test_monitor_timeout_is_used_for_the_request(client: TestClient, web: Web) -> None:
    monitor_id = create_monitor(client, timeout=2.5)

    run(client, monitor_id)

    assert web.requests[0].extensions["timeout"]["read"] == 2.5


def test_run_unknown_monitor_returns_404_without_request(
    client: TestClient, web: Web
) -> None:
    response = run(client, 999)

    assert response.status_code == 404
    assert response.json() == {
        "code": "monitor_not_found",
        "message": "Монитор 999 не найден",
    }
    assert web.requests == []


def test_run_with_non_integer_id_returns_422(client: TestClient) -> None:
    assert client.post("/monitors/abc/checks").status_code == 422


def test_check_ids_are_global_and_increasing(client: TestClient) -> None:
    first, second = create_monitor(client, "a.test"), create_monitor(client, "b.test")

    ids = [run(client, m).json()["id"] for m in (first, second, first)]

    assert ids == [1, 2, 3]


# --- GET /monitors/{id}/checks ---------------------------------------------


def test_list_is_empty_for_a_new_monitor(client: TestClient) -> None:
    monitor_id = create_monitor(client)

    response = client.get(f"/monitors/{monitor_id}/checks")

    assert response.status_code == 200
    assert response.json() == []


def test_list_returns_newest_first(client: TestClient) -> None:
    monitor_id = create_monitor(client)
    created = [run(client, monitor_id).json() for _ in range(3)]

    listed = client.get(f"/monitors/{monitor_id}/checks").json()

    assert listed == list(reversed(created))


def test_list_default_limit_is_twenty(client: TestClient) -> None:
    monitor_id = create_monitor(client)
    for _ in range(25):
        run(client, monitor_id)

    assert len(client.get(f"/monitors/{monitor_id}/checks").json()) == 20


def test_list_limit_picks_the_newest(client: TestClient) -> None:
    monitor_id = create_monitor(client)
    for _ in range(5):
        run(client, monitor_id)

    listed = client.get(f"/monitors/{monitor_id}/checks", params={"limit": 2}).json()

    assert [c["id"] for c in listed] == [5, 4]


@pytest.mark.parametrize("limit", ["0", "-1", "101", "abc", "1.5", ""])
def test_list_rejects_invalid_limit(client: TestClient, limit: str) -> None:
    monitor_id = create_monitor(client)

    response = client.get(f"/monitors/{monitor_id}/checks", params={"limit": limit})

    assert response.status_code == 422
    assert response.json()["errors"][0]["loc"] == ["query", "limit"]


@pytest.mark.parametrize("limit", [1, 50, 100])
def test_list_accepts_limit_bounds(client: TestClient, limit: int) -> None:
    monitor_id = create_monitor(client)

    response = client.get(f"/monitors/{monitor_id}/checks", params={"limit": limit})

    assert response.status_code == 200


def test_history_is_capped_per_monitor(client: TestClient) -> None:
    monitor_id = create_monitor(client)
    for _ in range(MAX_CHECKS_PER_MONITOR + 5):
        run(client, monitor_id)

    listed = client.get(
        f"/monitors/{monitor_id}/checks", params={"limit": MAX_CHECKS_PER_MONITOR}
    ).json()

    assert len(listed) == MAX_CHECKS_PER_MONITOR
    assert listed[0]["id"] == MAX_CHECKS_PER_MONITOR + 5
    assert listed[-1]["id"] == 6


def test_list_shows_only_checks_of_that_monitor(client: TestClient) -> None:
    first, second = create_monitor(client, "a.test"), create_monitor(client, "b.test")
    run(client, first)
    run(client, first)
    run(client, second)

    assert len(client.get(f"/monitors/{first}/checks").json()) == 2
    assert len(client.get(f"/monitors/{second}/checks").json()) == 1


def test_list_unknown_monitor_returns_404(client: TestClient) -> None:
    response = client.get("/monitors/999/checks")

    assert response.status_code == 404
    assert response.json()["code"] == "monitor_not_found"


# --- связь с монитором -----------------------------------------------------


def test_old_checks_keep_the_url_after_patch(client: TestClient) -> None:
    monitor_id = create_monitor(client, "old.test")
    run(client, monitor_id)
    client.patch(f"/monitors/{monitor_id}", json={"url": "new.test"})
    run(client, monitor_id)

    urls = [c["url"] for c in client.get(f"/monitors/{monitor_id}/checks").json()]

    assert urls == ["https://new.test", "https://old.test"]


def test_deleting_monitor_removes_its_checks(client: TestClient) -> None:
    monitor_id = create_monitor(client)
    run(client, monitor_id)

    client.delete(f"/monitors/{monitor_id}")

    assert client.get(f"/monitors/{monitor_id}/checks").status_code == 404
    assert run(client, monitor_id).status_code == 404


def test_recreated_monitor_does_not_inherit_old_checks(client: TestClient) -> None:
    old_id = create_monitor(client, "a.test")
    run(client, old_id)
    client.delete(f"/monitors/{old_id}")

    new_id = create_monitor(client, "a.test")

    assert new_id != old_id
    assert client.get(f"/monitors/{new_id}/checks").json() == []


def test_deleting_one_monitor_keeps_checks_of_others(client: TestClient) -> None:
    first, second = create_monitor(client, "a.test"), create_monitor(client, "b.test")
    run(client, first)
    run(client, second)

    client.delete(f"/monitors/{first}")

    assert len(client.get(f"/monitors/{second}/checks").json()) == 1


# --- приложение ------------------------------------------------------------


def test_http_client_is_created_in_lifespan_and_closed_on_exit() -> None:
    with TestClient(app):
        shared = app.state.http_client
        assert isinstance(shared, httpx.AsyncClient)
        assert not shared.is_closed

    assert shared.is_closed


def test_http_client_is_one_per_app_run() -> None:
    with TestClient(app) as first:
        first.get("/monitors")
        client_a = app.state.http_client
        first.get("/monitors")
        assert app.state.http_client is client_a

    with TestClient(app):
        assert app.state.http_client is not client_a


def test_openapi_documents_nested_routes_and_limit(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()
    path = spec["paths"]["/monitors/{monitor_id}/checks"]

    assert "201" in path["post"]["responses"]
    assert "404" in path["post"]["responses"]
    limit = next(p for p in path["get"]["parameters"] if p["name"] == "limit")
    assert (limit["schema"]["minimum"], limit["schema"]["maximum"]) == (1, 100)
    assert limit["schema"]["default"] == 20
