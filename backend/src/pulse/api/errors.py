import logging
from http import HTTPStatus

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from pulse.api.cookies import clear_refresh_cookie
from pulse.api.middlewares.request_id import REQUEST_ID_HEADER
from pulse.domain.errors import (
    AuthenticationError,
    DuplicateMonitorError,
    DuplicateUserError,
    MonitorNotFoundError,
    PermissionDeniedError,
    RateLimitExceededError,
    RefreshTokenError,
)

logger = logging.getLogger(__name__)


class ErrorResponse(BaseModel):
    """Единое тело ошибки: код для программы и сообщение для человека."""

    code: str
    message: str


class FieldError(BaseModel):
    loc: list[str | int]
    type: str
    msg: str


class ValidationErrorResponse(ErrorResponse):
    errors: list[FieldError]


async def monitor_not_found_handler(request: Request, exc: Exception) -> JSONResponse:
    # Сигнатура с `Exception` нужна Starlette; сюда попадает только
    # MonitorNotFoundError (см. регистрацию ниже), и его текст готов для клиента.
    body = ErrorResponse(code="monitor_not_found", message=str(exc))
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND, content=body.model_dump()
    )


async def duplicate_monitor_handler(request: Request, exc: Exception) -> JSONResponse:
    # Сюда попадает только DuplicateMonitorError (см. регистрацию ниже).
    body = ErrorResponse(code="monitor_already_exists", message=str(exc))
    return JSONResponse(status_code=status.HTTP_409_CONFLICT, content=body.model_dump())


async def duplicate_user_handler(request: Request, exc: Exception) -> JSONResponse:
    # Сюда попадает только DuplicateUserError (см. регистрацию ниже).
    body = ErrorResponse(code="user_already_exists", message=str(exc))
    return JSONResponse(status_code=status.HTTP_409_CONFLICT, content=body.model_dump())


async def authentication_error_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    assert isinstance(exc, AuthenticationError)
    body = ErrorResponse(code=exc.code, message=exc.message)
    # RFC 6750: ответ 401 обязан сообщать, какая схема аутентификации ожидается.
    response = JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content=body.model_dump(),
        headers={"WWW-Authenticate": "Bearer"},
    )
    if isinstance(exc, RefreshTokenError):
        # Мёртвый refresh-токен браузер больше не должен присылать.
        settings = request.app.state.settings
        clear_refresh_cookie(response, settings)
    return response


async def permission_denied_handler(request: Request, exc: Exception) -> JSONResponse:
    # Сюда попадает только PermissionDeniedError (см. регистрацию ниже).
    body = ErrorResponse(code="forbidden", message=str(exc))
    return JSONResponse(
        status_code=status.HTTP_403_FORBIDDEN, content=body.model_dump()
    )


async def rate_limit_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RateLimitExceededError)
    body = ErrorResponse(code="rate_limited", message=str(exc))
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content=body.model_dump(),
        headers={"Retry-After": str(exc.retry_after)},
    )


async def validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    # Берём только loc, type и msg. Стандартный ответ FastAPI эхом возвращает
    # `input`: с inf/NaN он не сериализуется (500), а с паролем стал бы утечкой.
    body = ValidationErrorResponse(
        code="validation_error",
        message="Запрос не прошёл проверку",
        errors=[
            FieldError(loc=list(e["loc"]), type=e["type"], msg=e["msg"])
            for e in exc.errors()
        ],
    )
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, content=body.model_dump()
    )


async def http_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    # Стандартные 404 (нет маршрута) и 405 (неверный метод) приходят сюда же.
    # Текст берём из статуса, а не из `exc.detail`: он может быть произвольным.
    phrase = HTTPStatus(exc.status_code).phrase
    body = ErrorResponse(code=phrase.lower().replace(" ", "_"), message=phrase)
    # headers нужны для 405: в них лежит Allow со списком допустимых методов.
    return JSONResponse(
        status_code=exc.status_code, content=body.model_dump(), headers=exc.headers
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    # Текст исключения клиенту не отдаём: в нём могут быть пути, SQL и данные.
    # Полная трассировка уходит в лог (и в stderr сервера: Starlette поднимет
    # исключение дальше после ответа).
    # Ответ 500 строится снаружи RequestIdMiddleware: contextvar уже сброшен, id
    # берём из scope (если middleware подключён).
    request_id: str | None = request.scope.get("state", {}).get("request_id")
    logger.error(
        "Необработанная ошибка", exc_info=exc, extra={"request_id": request_id or "-"}
    )
    body = ErrorResponse(code="internal_error", message="Внутренняя ошибка сервера")
    headers = {REQUEST_ID_HEADER: request_id} if request_id else None
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=body.model_dump(),
        headers=headers,
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(MonitorNotFoundError, monitor_not_found_handler)
    app.add_exception_handler(DuplicateMonitorError, duplicate_monitor_handler)
    app.add_exception_handler(DuplicateUserError, duplicate_user_handler)
    app.add_exception_handler(AuthenticationError, authentication_error_handler)
    app.add_exception_handler(PermissionDeniedError, permission_denied_handler)
    app.add_exception_handler(RateLimitExceededError, rate_limit_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)
