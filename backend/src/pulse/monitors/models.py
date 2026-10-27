from datetime import timedelta
from typing import Annotated

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    field_validator,
)

from pulse.utils.normalize_url import normalize_url
from pulse.utils.parse_interval import parse_interval


def _parse_interval_if_str(value: object) -> object:
    return parse_interval(value) if isinstance(value, str) else value


# Типы-с-валидацией: одно определение для файла конфигурации и для API.
Url = Annotated[str, AfterValidator(normalize_url)]
# В JSON интервал отдаётся секундами, а не ISO 8601 ("PT5M"): число принимается
# обратно на входе, так что ответ можно отправить в PATCH без преобразований.
Interval = Annotated[
    timedelta,
    BeforeValidator(_parse_interval_if_str),
    PlainSerializer(
        lambda value: value.total_seconds(), return_type=float, when_used="json"
    ),
]
Timeout = Annotated[float, Field(gt=0)]

DEFAULT_TIMEOUT = 10.0


class MonitorConfig(BaseModel):
    """Монитор из файла конфигурации."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    url: Url
    interval: Interval
    timeout: Timeout = DEFAULT_TIMEOUT


class MonitorCreate(BaseModel):
    """Тело POST /monitors."""

    model_config = ConfigDict(extra="forbid")

    url: Url
    interval: Interval
    timeout: Timeout = DEFAULT_TIMEOUT


class MonitorUpdate(BaseModel):
    """Тело PATCH /monitors/{id}: меняются только переданные поля.

    Отсутствие поля означает «не менять», а явный `null` запрещён: у монитора
    нет необязательных полей, которые можно очистить.
    """

    model_config = ConfigDict(extra="forbid")

    url: Url | None = None
    interval: Interval | None = None
    timeout: Timeout | None = None

    @field_validator("url", "interval", "timeout", mode="before")
    @classmethod
    def _reject_null(cls, value: object) -> object:
        if value is None:
            raise ValueError("значение не может быть null: уберите поле из запроса")
        return value


class MonitorRead(BaseModel):
    """Монитор в ответе API."""

    model_config = ConfigDict(frozen=True)

    id: int
    url: Url
    interval: Interval
    timeout: Timeout
