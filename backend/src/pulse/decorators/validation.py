import inspect
import math
from collections.abc import Callable
from functools import wraps
from typing import ParamSpec, TypeVar

P = ParamSpec("P")
R = TypeVar("R")


def _validate_argument(
    argument_name: str,
    check: Callable[[object], None],
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Общая основа декораторов: достаёт значение аргумента и отдаёт его в `check`.

    `check` сам бросает исключение, если значение не подходит. Значение берётся из
    `bound.arguments`: туда уже попали и позиционные, и именованные аргументы, и
    значения по умолчанию, поэтому ручной разбор `args` не нужен.
    """

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        sig = inspect.signature(func)

        if argument_name not in sig.parameters:
            raise ValueError(f"У {func.__name__} нет аргумента '{argument_name}'")

        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()
            check(bound.arguments[argument_name])

            return func(*args, **kwargs)

        return wrapper

    return decorator


def validate_not_empty(
    argument_name: str,
    message: str | None = None,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    error_msg = message or f"Ошибка: аргумент '{argument_name}' пуст!"

    def check(value: object) -> None:
        if not value:
            raise ValueError(error_msg)

    return _validate_argument(argument_name, check)


def validate_trimmed_not_empty(
    argument_name: str,
    message: str | None = None,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    error_msg = message or f"Ошибка: аргумент '{argument_name}' пуст!"

    def check(value: object) -> None:
        if isinstance(value, str):
            value = value.strip()

        if not value:
            raise ValueError(error_msg)

    return _validate_argument(argument_name, check)


def validate_range(
    argument_name: str,
    *,
    min_value: float | None = None,
    max_value: float | None = None,
    integer: bool = False,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Проверяет, что число лежит в `[min_value, max_value]` (обе границы включены).

    `None` вместо границы значит «без ограничения с этой стороны».
    `bool` отвергается: в Python он подкласс `int`, и `True` иначе прошёл бы как 1.
    `nan` отвергается: любое сравнение с ним ложно, границы он прошёл бы молча.
    `integer=True` требует именно `int`, дробные значения дают `TypeError`.
    """

    def check(value: object) -> None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"Аргумент '{argument_name}' должен быть числом float/int")

        if integer and not isinstance(value, int):
            raise TypeError(f"Аргумент '{argument_name}' должен быть целым числом")

        if isinstance(value, float) and math.isnan(value):
            raise ValueError(f"Аргумент '{argument_name}' не должен быть NaN")

        if min_value is not None and value < min_value:
            raise ValueError(
                f"Значение '{value}' аргумента '{argument_name}' "
                f"не должно быть меньше '{min_value}'"
            )

        if max_value is not None and value > max_value:
            raise ValueError(
                f"Значение '{value}' аргумента '{argument_name}' "
                f"не должно быть больше '{max_value}'"
            )

    return _validate_argument(argument_name, check)
