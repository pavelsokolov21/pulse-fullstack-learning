from datetime import timedelta
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    PlainSerializer,
    field_validator,
    model_validator,
)

from pulse.api.schemas.checks import CheckRead
from pulse.domain.monitors import ensure_timeout_fits_interval
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

    @model_validator(mode="after")
    def _timeout_fits_interval(self) -> "MonitorCreate":
        ensure_timeout_fits_interval(self.interval, self.timeout)
        return self


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
    owner_id: int
    url: str
    interval: IntervalSeconds
    timeout: float


class MonitorListItem(MonitorRead):
    """Строка таблицы мониторов: email владельца и последняя проверка (если была)."""

    owner_email: str
    last_check: CheckRead | None


class MonitorPage(BaseModel):
    """Страница списка. `total` нужен интерфейсу, чтобы показать число страниц."""

    model_config = ConfigDict(frozen=True)

    items: list[MonitorListItem]
    total: int
    limit: int
    offset: int
