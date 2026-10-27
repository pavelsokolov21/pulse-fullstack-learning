from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from pulse.monitors.errors import MonitorNotFoundError


class ErrorResponse(BaseModel):
    """Единое тело ошибки: код для программы и сообщение для человека."""

    code: str
    message: str


async def monitor_not_found_handler(request: Request, exc: Exception) -> JSONResponse:
    # Сигнатура с `Exception` нужна Starlette; сюда попадает только
    # MonitorNotFoundError (см. регистрацию ниже), и его текст готов для клиента.
    body = ErrorResponse(code="monitor_not_found", message=str(exc))
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND, content=body.model_dump()
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(MonitorNotFoundError, monitor_not_found_handler)
