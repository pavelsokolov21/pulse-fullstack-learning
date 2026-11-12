import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.routing import APIRoute

from pulse.api.export_openapi import build_schema
from pulse.api.main import create_app
from pulse.api.settings import ApiSettings

FRONTEND_SCHEMA = Path(__file__).parents[3] / "frontend" / "openapi.json"
METHODS = {"get", "post", "put", "patch", "delete"}


@pytest.fixture(scope="module")
def schema() -> dict[str, Any]:
    return build_schema()


def operations(schema: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """`operationId` -> описание операции."""
    return {
        op["operationId"]: op
        for item in schema["paths"].values()
        for method, op in item.items()
        if method in METHODS
    }


def operation(schema: dict[str, Any], method: str, path: str) -> dict[str, Any]:
    return schema["paths"][path][method]


def test_operation_ids_are_handler_names(schema: dict[str, Any]) -> None:
    assert set(operations(schema)) == {
        "register",
        "login",
        "refresh",
        "logout",
        "me",
        "list_monitors",
        "create_monitor",
        "get_monitor",
        "update_monitor",
        "delete_monitor",
        "run_check",
        "list_checks",
        "get_monitor_report",
        "run_concurrency_demo",
        "list_task_events",
    }


def test_operation_ids_are_unique() -> None:
    # Одинаковые имена обработчиков склеились бы в одну функцию клиента.
    app = create_app(ApiSettings(**dict[str, Any]()))
    names = [r.name for r in app.routes if isinstance(r, APIRoute)]

    assert len(names) == len(set(names))


def test_operation_ids_have_no_path_or_method_suffix(schema: dict[str, Any]) -> None:
    for operation_id in operations(schema):
        assert not operation_id.endswith(("_get", "_post", "_patch", "_delete"))
        assert "__" not in operation_id


def test_schema_names_follow_operation_ids(schema: dict[str, Any]) -> None:
    # Тело формы входа называлось Body_login_auth_login_post.
    assert "Body_login" in schema["components"]["schemas"]
    assert "Body_login_auth_login_post" not in schema["components"]["schemas"]


@pytest.mark.parametrize("code", ["401", "403", "404", "409", "422", "429"])
def test_every_error_status_is_described_somewhere(
    schema: dict[str, Any], code: str
) -> None:
    described = [code in op["responses"] for op in operations(schema).values()]

    assert any(described)


@pytest.mark.parametrize(
    ("method", "path", "codes"),
    [
        ("post", "/auth/register", {"201", "409", "422"}),
        ("post", "/auth/login", {"200", "401", "422", "429"}),
        ("post", "/auth/refresh", {"200", "401"}),
        ("get", "/auth/me", {"200", "401"}),
        ("get", "/monitors", {"200", "401"}),
        ("post", "/monitors", {"201", "401", "403", "409", "422"}),
        ("get", "/monitors/{monitor_id}", {"200", "401", "404", "422"}),
        ("patch", "/monitors/{monitor_id}", {"200", "401", "403", "404", "409", "422"}),
        ("delete", "/monitors/{monitor_id}", {"204", "401", "403", "404"}),
        ("post", "/monitors/{monitor_id}/checks", {"201", "401", "403", "404"}),
        ("get", "/monitors/{monitor_id}/checks", {"200", "401", "404"}),
    ],
)
def test_operations_describe_the_statuses_they_can_return(
    schema: dict[str, Any], method: str, path: str, codes: set[str]
) -> None:
    assert codes <= set(operation(schema, method, path)["responses"])


def test_only_login_is_rate_limited(schema: dict[str, Any]) -> None:
    limited = {
        op_id for op_id, op in operations(schema).items() if "429" in op["responses"]
    }

    assert limited == {"login"}


def test_429_documents_retry_after(schema: dict[str, Any]) -> None:
    response = operation(schema, "post", "/auth/login")["responses"]["429"]

    assert "Retry-After" in response["headers"]


def error_models(schema: dict[str, Any]) -> set[tuple[str, str, str]]:
    found: set[tuple[str, str, str]] = set()
    for op_id, op in operations(schema).items():
        for code, response in op["responses"].items():
            if not code.startswith(("4", "5")):
                continue
            ref = response["content"]["application/json"]["schema"]["$ref"]
            found.add((op_id, code, ref.rsplit("/", 1)[-1]))
    return found


def test_error_responses_use_the_single_error_format(schema: dict[str, Any]) -> None:
    for op_id, code, model in error_models(schema):
        expected = "ValidationErrorResponse" if code == "422" else "ErrorResponse"
        assert model == expected, (op_id, code)


def test_default_validation_error_schema_is_gone(schema: dict[str, Any]) -> None:
    # Реальное тело 422 у приложения своё, и клиент не должен типизировать чужое.
    assert "HTTPValidationError" not in schema["components"]["schemas"]
    assert "ValidationError" not in schema["components"]["schemas"]


def test_login_body_is_a_form_not_json(schema: dict[str, Any]) -> None:
    content = operation(schema, "post", "/auth/login")["requestBody"]["content"]

    assert list(content) == ["application/x-www-form-urlencoded"]


def test_access_token_goes_in_the_body_and_refresh_in_a_cookie(
    schema: dict[str, Any],
) -> None:
    token = schema["components"]["schemas"]["TokenResponse"]

    assert "access_token" in token["properties"]
    assert "refresh_token" not in token["properties"]


def test_bearer_scheme_is_declared(schema: dict[str, Any]) -> None:
    assert "OAuth2PasswordBearer" in schema["components"]["securitySchemes"]


def test_checked_in_frontend_schema_is_up_to_date(schema: dict[str, Any]) -> None:
    # Если тест упал, API изменился, а клиент фронтенда нет: `npm run gen:api`.
    on_disk = json.loads(FRONTEND_SCHEMA.read_text(encoding="utf-8"))

    assert on_disk == schema, "frontend/openapi.json устарел, выполните npm run gen:api"
