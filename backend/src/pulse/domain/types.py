from datetime import timedelta
from typing import Annotated

from pydantic import AfterValidator, BeforeValidator, Field

from pulse.core.utils.normalize_url import normalize_url
from pulse.core.utils.parse_interval import parse_interval


def _parse_interval_if_str(value: object) -> object:
    # bool это int, и без явной проверки `true` стал бы интервалом в 1 секунду.
    if isinstance(value, bool):
        raise ValueError("интервал должен быть строкой ('5m') или числом секунд")
    return parse_interval(value) if isinstance(value, str) else value


def _ensure_positive(value: timedelta) -> timedelta:
    # Проверка на самом типе, а не в Field(gt=timedelta(0)): так она работает и для
    # строки, и для числа, а в OpenAPI не попадает мусорное `gt: 'PT0S'`.
    if value <= timedelta(0):
        raise ValueError("интервал должен быть положительным")
    return value


# Типы с валидацией: одно определение для файла конфигурации и для API.
Url = Annotated[str, AfterValidator(normalize_url)]
Interval = Annotated[
    timedelta,
    # json_schema_input_type: в OpenAPI вход описан как строка или число секунд,
    # а не как ISO 8601 duration, который принимает сам timedelta.
    BeforeValidator(_parse_interval_if_str, json_schema_input_type=str | float),
    AfterValidator(_ensure_positive),
]
# inf и NaN отвергаем: JSON их не умеет, и ответ с ними не сериализуется.
Timeout = Annotated[float, Field(gt=0, allow_inf_nan=False)]

DEFAULT_TIMEOUT = 10.0
