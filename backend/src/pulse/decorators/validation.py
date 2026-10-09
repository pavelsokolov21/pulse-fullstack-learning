import inspect
from collections.abc import Callable
from functools import wraps
from typing import ParamSpec, TypeVar

P = ParamSpec("P")
R = TypeVar("R")


def validate_not_empty(argument_name: str, message: str | None = None):

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        sig = inspect.signature(func)

        if argument_name not in sig.parameters:
            raise ValueError(f"У {func.__name__} нет аргумента '{argument_name}'")

        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()
            arg_value = bound.arguments[argument_name]

            if arg_value is None and args:
                arg_value = args[0]

            if not arg_value:
                error_msg = message or f"Ошибка: аргумент '{argument_name}' пуст!"
                raise ValueError(error_msg)

            return func(*args, **kwargs)

        return wrapper

    return decorator


def validate_range(
    argument_name: str,
    range: tuple[float | None, float | None] = (None, None),
):
    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        sig = inspect.signature(func)

        if argument_name not in sig.parameters:
            raise ValueError(f"У {func.__name__} нет аргумента '{argument_name}'")

        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()
            arg_value = bound.arguments[argument_name]

            if not isinstance(arg_value, (int, float)):
                raise TypeError(
                    f"Аргумент '{argument_name}' должен быть числом float/int"
                )

            min, max = range

            if min is not None and arg_value < min:
                raise ValueError(f"Значение '{arg_value}' должно быть больше '{min}'")

            if max is not None and arg_value > max:
                raise ValueError(f"Значение '{arg_value}' должно быть меньше '{max}'")

            return func(*args, **kwargs)

        return wrapper

    return decorator
