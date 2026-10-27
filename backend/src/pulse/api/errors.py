import logging
from http import HTTPStatus

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from pulse.monitors.errors import MonitorNotFoundError

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
    logger.error("Необработанная ошибка", exc_info=exc)
    body = ErrorResponse(code="internal_error", message="Внутренняя ошибка сервера")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content=body.model_dump()
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(MonitorNotFoundError, monitor_not_found_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)
