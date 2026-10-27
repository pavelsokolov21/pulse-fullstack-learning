from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from pulse.api.deps import get_store
from pulse.api.main import app
from pulse.monitors.store import MonitorStore

NOT_FOUND_BODY = {"code": "monitor_not_found", "message": "Монитор 999 не найден"}


@pytest.fixture
def client() -> Iterator[TestClient]:
    # `with` запускает lifespan: у каждого теста своё чистое хранилище.
    with TestClient(app) as test_client:
        yield test_client


def create(client: TestClient, **overrides: object) -> dict[str, object]:
    body = {"url": "example.com", "interval": "5m", **overrides}
    response = client.post("/monitors", json=body)
    assert response.status_code == 201
    return response.json()


# --- POST /monitors --------------------------------------------------------


def test_create_returns_201_with_id_and_normalized_fields(client: TestClient) -> None:
    response = client.post(
        "/monitors", json={"url": "Example.COM/Path", "interval": "5m", "timeout": 3}
    )

    assert response.status_code == 201
    assert response.json() == {
        "id": 1,
        "url": "https://example.com/Path",
        "interval": 300.0,
        "timeout": 3.0,
    }


def test_create_uses_default_timeout(client: TestClient) -> None:
    assert create(client)["timeout"] == 10.0


def test_ids_are_sequential_and_not_reused_after_delete(client: TestClient) -> None:
    first = create(client)
    client.delete(f"/monitors/{first['id']}")

    assert create(client)["id"] == 2


@pytest.mark.parametrize(
    "body",
    [
        {"interval": "5m"},
        {"url": "example.com"},
        {"url": "", "interval": "5m"},
        {"url": "ftp://example.com", "interval": "5m"},
        {"url": "example.com", "interval": "0s"},
        {"url": "example.com", "interval": "abc"},
        {"url": "example.com", "interval": "5m", "timeout": 0},
        {"url": "example.com", "interval": "5m", "id": 7},
        {"url": "example.com", "interval": "5m", "retries": 3},
    ],
    ids=[
        "no-url",
        "no-interval",
        "empty-url",
        "bad-scheme",
        "zero-interval",
        "bad-interval",
        "zero-timeout",
        "client-sets-id",
        "unknown-field",
    ],
)
def test_create_rejects_invalid_body_with_422(
    client: TestClient, body: dict[str, object]
) -> None:
    response = client.post("/monitors", json=body)

    assert response.status_code == 422
    assert client.get("/monitors").json() == []


# --- GET /monitors ---------------------------------------------------------


def test_list_is_empty_at_start(client: TestClient) -> None:
    response = client.get("/monitors")

    assert response.status_code == 200
    assert response.json() == []


def test_list_returns_monitors_in_creation_order(client: TestClient) -> None:
    create(client, url="a.test")
    create(client, url="b.test")

    urls = [m["url"] for m in client.get("/monitors").json()]

    assert urls == ["https://a.test", "https://b.test"]


# --- GET /monitors/{id} ----------------------------------------------------


def test_get_returns_the_monitor(client: TestClient) -> None:
    created = create(client)

    response = client.get(f"/monitors/{created['id']}")

    assert response.status_code == 200
    assert response.json() == created


def test_get_unknown_id_returns_404_with_error_body(client: TestClient) -> None:
    response = client.get("/monitors/999")

    assert response.status_code == 404
    assert response.json() == NOT_FOUND_BODY


def test_get_non_integer_id_returns_422(client: TestClient) -> None:
    assert client.get("/monitors/abc").status_code == 422


# --- PATCH /monitors/{id} --------------------------------------------------


def test_patch_changes_only_given_fields(client: TestClient) -> None:
    created = create(client, timeout=3)

    response = client.patch(f"/monitors/{created['id']}", json={"interval": "1h"})

    assert response.status_code == 200
    assert response.json() == {**created, "interval": 3600.0}


def test_patch_values_are_validated_and_normalized(client: TestClient) -> None:
    created = create(client)

    response = client.patch(f"/monitors/{created['id']}", json={"url": "NEW.test/"})

    assert response.json()["url"] == "https://new.test"


def test_patch_result_is_persisted(client: TestClient) -> None:
    created = create(client)
    client.patch(f"/monitors/{created['id']}", json={"timeout": 7})

    assert client.get(f"/monitors/{created['id']}").json()["timeout"] == 7.0


def test_patch_does_not_touch_other_monitors(client: TestClient) -> None:
    first = create(client, url="a.test")
    second = create(client, url="b.test")

    client.patch(f"/monitors/{first['id']}", json={"url": "changed.test"})

    assert client.get(f"/monitors/{second['id']}").json() == second


def test_empty_patch_changes_nothing(client: TestClient) -> None:
    created = create(client)

    response = client.patch(f"/monitors/{created['id']}", json={})

    assert response.status_code == 200
    assert response.json() == created


def test_response_fields_can_be_sent_back_to_patch(client: TestClient) -> None:
    # Интервал читается секундами и принимается секундами: круг замыкается.
    created = create(client, interval="5m", timeout=3)
    body = {k: v for k, v in created.items() if k != "id"}

    response = client.patch(f"/monitors/{created['id']}", json=body)

    assert response.status_code == 200
    assert response.json() == created


@pytest.mark.parametrize(
    "body",
    [
        {"url": None},
        {"interval": None},
        {"timeout": None},
        {"url": ""},
        {"interval": "0s"},
        {"timeout": -1},
        {"id": 5},
        {"retries": 1},
    ],
    ids=[
        "null-url",
        "null-interval",
        "null-timeout",
        "empty-url",
        "zero-interval",
        "negative-timeout",
        "id",
        "unknown-field",
    ],
)
def test_patch_rejects_invalid_body_and_keeps_monitor(
    client: TestClient, body: dict[str, object]
) -> None:
    created = create(client)

    response = client.patch(f"/monitors/{created['id']}", json=body)

    assert response.status_code == 422
    assert client.get(f"/monitors/{created['id']}").json() == created


def test_patch_unknown_id_returns_404_with_error_body(client: TestClient) -> None:
    response = client.patch("/monitors/999", json={"timeout": 1})

    assert response.status_code == 404
    assert response.json() == NOT_FOUND_BODY


# --- DELETE /monitors/{id} -------------------------------------------------


def test_delete_returns_204_without_body_and_removes_monitor(
    client: TestClient,
) -> None:
    created = create(client)

    response = client.delete(f"/monitors/{created['id']}")

    assert response.status_code == 204
    assert response.content == b""
    assert client.get(f"/monitors/{created['id']}").status_code == 404


def test_delete_keeps_other_monitors(client: TestClient) -> None:
    first = create(client, url="a.test")
    second = create(client, url="b.test")

    client.delete(f"/monitors/{first['id']}")

    assert client.get("/monitors").json() == [second]


def test_delete_unknown_id_returns_404_with_error_body(client: TestClient) -> None:
    response = client.delete("/monitors/999")

    assert response.status_code == 404
    assert response.json() == NOT_FOUND_BODY


def test_second_delete_returns_404(client: TestClient) -> None:
    created = create(client)
    client.delete(f"/monitors/{created['id']}")

    assert client.delete(f"/monitors/{created['id']}").status_code == 404


# --- хранилище и зависимости -----------------------------------------------


def test_each_app_start_gets_a_fresh_store() -> None:
    with TestClient(app) as first:
        create(first)

    with TestClient(app) as second:
        assert second.get("/monitors").json() == []


def test_store_is_injected_through_depends() -> None:
    store = MonitorStore()
    app.dependency_overrides[get_store] = lambda: store
    try:
        with TestClient(app) as client:
            create(client, url="injected.test")
    finally:
        app.dependency_overrides.clear()

    assert [m.url for m in store.list_all()] == ["https://injected.test"]


def test_openapi_documents_statuses_and_error_model(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]

    assert "201" in paths["/monitors"]["post"]["responses"]
    assert "204" in paths["/monitors/{monitor_id}"]["delete"]["responses"]
    for method in ("get", "patch", "delete"):
        assert "404" in paths["/monitors/{monitor_id}"][method]["responses"]


# --- числовой interval и нечисловые литералы -------------------------------


def post_raw(client: TestClient, raw_body: str):
    # Infinity и NaN не умеет собрать json=…: отправляем текст как есть.
    return client.post(
        "/monitors", content=raw_body, headers={"content-type": "application/json"}
    )


@pytest.mark.parametrize("interval", ["0", "-5", "true", "false", '"0s"', "0.0"])
def test_create_rejects_non_positive_or_boolean_interval(
    client: TestClient, interval: str
) -> None:
    response = post_raw(client, f'{{"url": "a.test", "interval": {interval}}}')

    assert response.status_code == 422
    assert client.get("/monitors").json() == []


def test_create_accepts_numeric_interval_in_seconds(client: TestClient) -> None:
    response = post_raw(client, '{"url": "a.test", "interval": 90}')

    assert response.status_code == 201
    assert response.json()["interval"] == 90.0


@pytest.mark.parametrize("literal", ["Infinity", "-Infinity", "NaN"])
def test_non_finite_timeout_returns_422_not_500(
    client: TestClient, literal: str
) -> None:
    body = f'{{"url": "a.test", "interval": "5m", "timeout": {literal}}}'

    response = post_raw(client, body)

    assert response.status_code == 422
    assert response.json()["errors"][0]["loc"] == ["body", "timeout"]


@pytest.mark.parametrize("literal", ["Infinity", "NaN"])
def test_non_finite_interval_returns_422_not_500(
    client: TestClient, literal: str
) -> None:
    assert (
        post_raw(client, f'{{"url": "a.test", "interval": {literal}}}').status_code
        == 422
    )


def test_non_finite_patch_returns_422_and_keeps_monitor(client: TestClient) -> None:
    created = create(client)

    response = client.patch(
        f"/monitors/{created['id']}",
        content='{"timeout": Infinity}',
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 422
    assert client.get(f"/monitors/{created['id']}").json() == created


# --- формат ошибки валидации -----------------------------------------------


def test_validation_error_uses_unified_body(client: TestClient) -> None:
    response = client.post("/monitors", json={"url": "", "interval": "5m"})

    body = response.json()
    assert response.status_code == 422
    assert body["code"] == "validation_error"
    assert isinstance(body["message"], str)
    assert [e["loc"] for e in body["errors"]] == [["body", "url"]]
    assert set(body["errors"][0]) == {"loc", "type", "msg"}


def test_validation_error_does_not_echo_input(client: TestClient) -> None:
    secret = "super-secret-value"

    response = client.post(
        "/monitors", json={"url": "a.test", "interval": "5m", "password": secret}
    )

    assert response.status_code == 422
    assert secret not in response.text


def test_malformed_json_returns_unified_422(client: TestClient) -> None:
    response = post_raw(client, "{not json")

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


def test_every_field_error_is_reported(client: TestClient) -> None:
    response = client.post(
        "/monitors", json={"url": "", "interval": "0s", "timeout": -1}
    )

    locs = sorted(tuple(e["loc"]) for e in response.json()["errors"])
    assert locs == [("body", "interval"), ("body", "timeout"), ("body", "url")]


# --- схема OpenAPI ---------------------------------------------------------


def test_openapi_describes_interval_input_as_string_or_number(
    client: TestClient,
) -> None:
    schemas = client.get("/openapi.json").json()["components"]["schemas"]

    for name in ("MonitorCreate", "MonitorUpdate"):
        interval = schemas[name]["properties"]["interval"]
        options = interval.get("anyOf", [])
        assert {"string", "number"} <= {o.get("type") for o in options}


def test_openapi_interval_has_no_duration_garbage(client: TestClient) -> None:
    text = client.get("/openapi.json").text

    assert "PT0S" not in text
    assert "duration" not in text


def test_openapi_documents_unified_validation_error(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()
    response = spec["paths"]["/monitors"]["post"]["responses"]["422"]

    ref = response["content"]["application/json"]["schema"]["$ref"]
    assert ref.endswith("/ValidationErrorResponse")
