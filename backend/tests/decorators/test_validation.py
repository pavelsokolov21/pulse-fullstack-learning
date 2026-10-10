import pytest

from pulse.decorators.validation import (
    validate_not_empty,
    validate_range,
    validate_trimmed_not_empty,
)


class Service:
    """Метод: первым позиционным аргументом приходит self (он truthy)."""

    @validate_not_empty("value")
    def run(self, value: object) -> str:
        return "ok"


# --- validate_not_empty ---------------------------------------------------


@pytest.mark.parametrize("value", [None, "", [], (), {}])
def test_not_empty_rejects_empty_values(value: object) -> None:
    @validate_not_empty("value")
    def func(value: object) -> str:
        return "ok"

    with pytest.raises(ValueError, match="пуст"):
        func(value)


@pytest.mark.parametrize("value", ["a", [0], (None,), {"k": 1}])
def test_not_empty_accepts_non_empty_values(value: object) -> None:
    @validate_not_empty("value")
    def func(value: object) -> str:
        return "ok"

    assert func(value) == "ok"


def test_not_empty_rejects_none_passed_positionally_to_method() -> None:
    # Регрессия: раньше None подменялся на args[0], то есть на self.
    with pytest.raises(ValueError, match="пуст"):
        Service().run(None)


def test_not_empty_rejects_none_passed_by_keyword_to_method() -> None:
    with pytest.raises(ValueError, match="пуст"):
        Service().run(value=None)


def test_not_empty_checks_default_value() -> None:
    @validate_not_empty("value")
    def func(value: object = None) -> str:
        return "ok"

    with pytest.raises(ValueError, match="пуст"):
        func()


def test_not_empty_uses_custom_message() -> None:
    @validate_not_empty("value", message="Нужно значение")
    def func(value: object) -> str:
        return "ok"

    with pytest.raises(ValueError, match="Нужно значение"):
        func("")


def test_decorator_rejects_unknown_argument_name_at_decoration_time() -> None:
    with pytest.raises(ValueError, match="missing"):

        @validate_not_empty("missing")
        def func(value: object) -> None: ...


def test_decorator_preserves_function_metadata() -> None:
    @validate_not_empty("value")
    def func(value: object) -> str:
        """Docstring."""
        return "ok"

    assert func.__name__ == "func"
    assert func.__doc__ == "Docstring."


# --- validate_trimmed_not_empty -------------------------------------------


@pytest.mark.parametrize("value", [None, "", "   ", "\t\n"])
def test_trimmed_rejects_blank_values(value: object) -> None:
    @validate_trimmed_not_empty("value")
    def func(value: object) -> str:
        return "ok"

    with pytest.raises(ValueError, match="пуст"):
        func(value)


def test_trimmed_accepts_padded_text() -> None:
    @validate_trimmed_not_empty("value")
    def func(value: object) -> str:
        return "ok"

    assert func("  a  ") == "ok"


def test_trimmed_rejects_none_passed_to_method() -> None:
    class Holder:
        @validate_trimmed_not_empty("value")
        def run(self, value: object) -> str:
            return "ok"

    with pytest.raises(ValueError, match="пуст"):
        Holder().run(None)


# --- validate_range -------------------------------------------------------


@validate_range("n", min_value=1, max_value=5)
def bounded(n: object) -> object:
    return n


@pytest.mark.parametrize("value", [1, 5, 3, 1.0, 4.999])
def test_range_accepts_values_inside_inclusive_bounds(value: object) -> None:
    assert bounded(value) == value


@pytest.mark.parametrize("value", [0, 0.999, 5.001, 6, -1])
def test_range_rejects_values_outside_bounds(value: object) -> None:
    with pytest.raises(ValueError, match="'n'"):
        bounded(value)


@pytest.mark.parametrize("value", [True, False, "3", None, [3]])
def test_range_rejects_non_numbers_including_bool(value: object) -> None:
    # bool это подкласс int, поэтому isinstance(True, int) is True.
    with pytest.raises(TypeError):
        bounded(value)


def test_range_rejects_nan() -> None:
    # Любое сравнение с nan ложно, поэтому проверку границ nan проходит молча.
    with pytest.raises(ValueError, match="NaN"):
        bounded(float("nan"))


def test_range_with_only_min() -> None:
    @validate_range("n", min_value=1)
    def func(n: object) -> object:
        return n

    assert func(10**12) == 10**12
    with pytest.raises(ValueError, match="'n'"):
        func(0)


def test_range_with_only_max() -> None:
    @validate_range("n", max_value=5)
    def func(n: object) -> object:
        return n

    assert func(-100) == -100
    with pytest.raises(ValueError, match="'n'"):
        func(6)


def test_range_integer_flag_rejects_float() -> None:
    @validate_range("n", min_value=1, integer=True)
    def func(n: object) -> object:
        return n

    assert func(2) == 2
    with pytest.raises(TypeError):
        func(1.5)
    with pytest.raises(TypeError):
        func(2.0)
