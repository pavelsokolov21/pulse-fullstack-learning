from datetime import timedelta
from typing import Annotated

from pydantic import BaseModel, ConfigDict, PlainSerializer, field_validator

from pulse.domain.types import DEFAULT_TIMEOUT, Interval, Timeout, Url

# В JSON интервал отдаётся секундами, а не ISO 8601 ("PT5M"): число принимается
# обратно на входе, так что ответ можно отправить в PATCH без преобразований.
IntervalSeconds = Annotated[
    timedelta,
    PlainSerializer(lambda value: value.total_seconds(), return_type=float),
]


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
    """Монитор в ответе API. Данные из хранилища уже валидны: не проверяем снова."""

    model_config = ConfigDict(frozen=True, from_attributes=True)

    id: int
    url: str
    interval: IntervalSeconds
    timeout: float
