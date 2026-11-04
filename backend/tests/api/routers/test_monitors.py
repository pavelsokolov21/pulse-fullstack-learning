from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from pulse.api.deps import get_monitor_repository, get_monitor_service
from pulse.api.main import app
from pulse.domain.monitors import Monitor, MonitorWithOwner
from pulse.domain.pagination import Page
from pulse.domain.users import User

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
        "owner_id": 1,
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
    assert client.get("/monitors").json()["items"] == []


# --- GET /monitors ---------------------------------------------------------


def test_list_is_empty_at_start(client: TestClient) -> None:
    response = client.get("/monitors")

    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0, "limit": 20, "offset": 0}


def test_list_returns_monitors_in_creation_order(client: TestClient) -> None:
    create(client, url="a.test")
    create(client, url="b.test")

    urls = [m["url"] for m in client.get("/monitors").json()["items"]]

    assert urls == ["https://a.test", "https://b.test"]


def test_list_items_carry_the_owner_email(client: TestClient) -> None:
    create(client, url="a.test")

    [item] = client.get("/monitors").json()["items"]

    assert item["owner_email"] == "tester@example.com"
    assert item["owner_id"] == 1


def test_list_is_paginated_with_total(client: TestClient) -> None:
    for n in range(5):
        create(client, url=f"{n}.test")

    body = client.get("/monitors", params={"limit": 2, "offset": 2}).json()

    assert (body["total"], body["limit"], body["offset"]) == (5, 2, 2)
    assert [m["url"] for m in body["items"]] == ["https://2.test", "https://3.test"]


def test_last_page_may_be_partial_and_the_one_after_it_empty(
    client: TestClient,
) -> None:
    for n in range(3):
        create(client, url=f"{n}.test")

    last = client.get("/monitors", params={"limit": 2, "offset": 2}).json()
    beyond = client.get("/monitors", params={"limit": 2, "offset": 4}).json()

    assert len(last["items"]) == 1
    assert (beyond["items"], beyond["total"]) == ([], 3)


@pytest.mark.parametrize(
    "params",
    [{"limit": 0}, {"limit": 101}, {"limit": -1}, {"offset": -1}, {"limit": "x"}],
)
def test_list_rejects_invalid_pagination(
    client: TestClient, params: dict[str, Any]
) -> None:
    assert client.get("/monitors", params=params).status_code == 422


def test_list_accepts_the_maximum_page_size(client: TestClient) -> None:
    assert client.get("/monitors", params={"limit": 100}).status_code == 200


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
    body = {k: v for k, v in created.items() if k not in ("id", "owner_id")}

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

    assert [m["id"] for m in client.get("/monitors").json()["items"]] == [second["id"]]


def test_delete_unknown_id_returns_404_with_error_body(client: TestClient) -> None:
    response = client.delete("/monitors/999")

    assert response.status_code == 404
    assert response.json() == NOT_FOUND_BODY


def test_second_delete_returns_404(client: TestClient) -> None:
    created = create(client)
    client.delete(f"/monitors/{created['id']}")

    assert client.delete(f"/monitors/{created['id']}").status_code == 404


# --- хранилище и зависимости -----------------------------------------------


def test_data_survives_an_app_restart() -> None:
    # Данные лежат в PostgreSQL, а не в памяти приложения.
    with TestClient(app) as first:
        created = create(first)

    with TestClient(app) as second:
        assert [m["id"] for m in second.get("/monitors").json()["items"]] == [
            created["id"]
        ]


def test_repository_is_injected_through_depends() -> None:
    # Подмена репозитория достаточна: сервис собирается поверх него.
    class FakeRepository:
        def __init__(self) -> None:
            self.created: list[str] = []

        async def create(
            self, *, owner_id: int, url: str, interval: timedelta, timeout: float
        ) -> Monitor:
            self.created.append(url)
            return Monitor(1, owner_id, url, interval, timeout)

    repository = FakeRepository()
    app.dependency_overrides[get_monitor_repository] = lambda: repository
    try:
        with TestClient(app) as client:
            create(client, url="injected.test")
    finally:
        app.dependency_overrides.pop(get_monitor_repository, None)

    assert repository.created == ["https://injected.test"]


def test_router_works_through_a_fake_service() -> None:
    class FakeService:
        async def list_page(
            self, actor: User, *, limit: int, offset: int
        ) -> Page[MonitorWithOwner]:
            item = MonitorWithOwner(
                7, actor.id, "https://fake.test", timedelta(seconds=5), 1.0, "o@x.io"
            )
            return Page([item], total=41)

    app.dependency_overrides[get_monitor_service] = lambda: FakeService()
    try:
        with TestClient(app) as client:
            body = client.get("/monitors").json()
    finally:
        app.dependency_overrides.pop(get_monitor_service, None)

    assert body["total"] == 41
    assert [m["id"] for m in body["items"]] == [7]
    assert body["items"][0]["owner_email"] == "o@x.io"


def test_created_monitor_is_stored_in_postgres(
    client: TestClient, admin_db: psycopg.Connection[tuple[object, ...]]
) -> None:
    created = create(client, url="a.test", timeout=3)

    # Прямой запрос суперпользователя другим соединением: значит, коммит состоялся
    # до отправки ответа, а не остался в незавершённой транзакции.
    row = admin_db.execute(
        "SELECT url, interval_seconds, timeout_seconds FROM monitors WHERE id = %s",
        (created["id"],),
    ).fetchone()
    assert row == ("https://a.test", 300, 3.0)


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
    assert client.get("/monitors").json()["items"] == []


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


# --- дубликаты URL (409) ---------------------------------------------------

DUPLICATE_CODE = "monitor_already_exists"


def test_create_duplicate_returns_409_with_error_body(client: TestClient) -> None:
    first = create(client, url="a.test")

    response = client.post("/monitors", json={"url": "a.test", "interval": "1h"})

    assert response.status_code == 409
    assert response.json() == {
        "code": DUPLICATE_CODE,
        "message": f"Монитор с URL https://a.test уже существует (id {first['id']})",
    }


@pytest.mark.parametrize(
    "variant",
    ["A.TEST", "https://a.test", "https://a.test/", "  a.test  ", "a.test:443"],
)
def test_urls_are_compared_after_normalization(
    client: TestClient, variant: str
) -> None:
    create(client, url="a.test")

    response = client.post("/monitors", json={"url": variant, "interval": "5m"})

    assert response.status_code == 409


@pytest.mark.parametrize(
    "other", ["a.test/path", "http://a.test", "b.test", "a.test:8443"]
)
def test_different_urls_are_not_duplicates(client: TestClient, other: str) -> None:
    create(client, url="a.test")

    assert (
        client.post("/monitors", json={"url": other, "interval": "5m"}).status_code
        == 201
    )


def test_rejected_duplicate_is_not_stored(client: TestClient) -> None:
    create(client, url="a.test")
    client.post("/monitors", json={"url": "a.test", "interval": "5m"})

    assert client.get("/monitors").json()["total"] == 1


def test_url_can_be_created_again_after_delete(client: TestClient) -> None:
    first = create(client, url="a.test")
    client.delete(f"/monitors/{first['id']}")

    assert (
        client.post("/monitors", json={"url": "a.test", "interval": "5m"}).status_code
        == 201
    )


def test_patch_to_another_monitors_url_returns_409(client: TestClient) -> None:
    first = create(client, url="a.test")
    second = create(client, url="b.test")

    response = client.patch(f"/monitors/{second['id']}", json={"url": "A.test/"})

    assert response.status_code == 409
    assert response.json()["code"] == DUPLICATE_CODE
    assert str(first["id"]) in response.json()["message"]
    assert client.get(f"/monitors/{second['id']}").json() == second


def test_patch_with_its_own_url_is_not_a_conflict(client: TestClient) -> None:
    created = create(client, url="a.test")

    response = client.patch(
        f"/monitors/{created['id']}", json={"url": "https://a.test", "timeout": 4}
    )

    assert response.status_code == 200
    assert response.json()["timeout"] == 4.0


def test_patch_unknown_id_with_taken_url_is_404(client: TestClient) -> None:
    create(client, url="a.test")

    assert client.patch("/monitors/999", json={"url": "a.test"}).status_code == 404


def test_openapi_documents_409_for_create_and_patch(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]

    assert "409" in paths["/monitors"]["post"]["responses"]
    assert "409" in paths["/monitors/{monitor_id}"]["patch"]["responses"]
    assert "409" not in paths["/monitors"]["get"]["responses"]


def test_url_that_is_a_prefix_of_an_existing_one_is_not_a_duplicate(
    client: TestClient,
) -> None:
    create(client, url="a.test/path")

    response = client.post("/monitors", json={"url": "a.test", "interval": "5m"})

    assert response.status_code == 201


# --- границы интервала и согласованность с таймаутом ------------------------


def test_create_rejects_interval_below_the_minimum(client: TestClient) -> None:
    response = client.post("/monitors", json={"url": "a.test", "interval": "29s"})

    assert response.status_code == 422
    (error,) = response.json()["errors"]
    assert error["loc"] == ["body", "interval"]
    assert "от 30 секунд до 24 часов" in error["msg"]
    assert client.get("/monitors").json()["items"] == []


@pytest.mark.parametrize("interval", ["25h", 86401, 30.5])
def test_create_rejects_other_invalid_intervals(
    client: TestClient, interval: object
) -> None:
    response = client.post("/monitors", json={"url": "a.test", "interval": interval})

    assert response.status_code == 422


def test_create_accepts_the_bounds(client: TestClient) -> None:
    low = create(client, url="low.test", interval="30s")
    high = create(client, url="high.test", interval="24h")

    assert (low["interval"], high["interval"]) == (30.0, 86400.0)


def test_create_rejects_timeout_not_shorter_than_interval(client: TestClient) -> None:
    response = client.post(
        "/monitors", json={"url": "a.test", "interval": "30s", "timeout": 30}
    )

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "validation_error"
    assert "короче интервала" in body["errors"][0]["msg"]


def test_patch_rejects_timeout_that_does_not_fit_the_stored_interval(
    client: TestClient,
) -> None:
    created = create(client, interval="1m")  # таймаут по умолчанию 10

    response = client.patch(f"/monitors/{created['id']}", json={"timeout": 60})

    assert response.status_code == 422
    assert response.json() == {
        "code": "validation_error",
        "message": "Запрос не прошёл проверку",
        "errors": [
            {
                "loc": ["body", "timeout"],
                "type": "value_error",
                "msg": "таймаут должен быть короче интервала проверки",
            }
        ],
    }
    assert client.get(f"/monitors/{created['id']}").json() == created


def test_patch_rejects_interval_shorter_than_the_stored_timeout(
    client: TestClient,
) -> None:
    created = create(client, interval="5m", timeout=100)

    response = client.patch(f"/monitors/{created['id']}", json={"interval": "1m"})

    assert response.status_code == 422
    assert response.json()["errors"][0]["loc"] == ["body", "interval"]
    assert client.get(f"/monitors/{created['id']}").json() == created


def test_patch_accepts_a_consistent_pair(client: TestClient) -> None:
    created = create(client, interval="5m", timeout=100)

    response = client.patch(
        f"/monitors/{created['id']}", json={"interval": "1m", "timeout": 50}
    )

    assert response.status_code == 200
    assert (response.json()["interval"], response.json()["timeout"]) == (60.0, 50.0)


def test_patch_of_unknown_monitor_is_404_even_with_invalid_values(
    client: TestClient,
) -> None:
    response = client.patch("/monitors/999", json={"timeout": 100000})

    assert response.status_code == 404
