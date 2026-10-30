import tomllib
from pathlib import Path
from typing import cast

from pulse.domain.monitors import MonitorConfig


def load_monitors(path: Path) -> list[MonitorConfig]:
    """Читает мониторы из TOML-файла с секциями `[[monitors]]`.

    Файл без мониторов (нет ключа `monitors` или список пуст) это ошибка, а не
    пустой результат: с таким конфигом проверять нечего. Неверный TOML даёт
    `tomllib.TOMLDecodeError`, неверное поле монитора `pydantic.ValidationError`
    (оба наследуются от `ValueError`).
    """
    # tomllib читает только бинарный поток: кодировку (UTF-8) определяет он сам.
    with path.open("rb") as file:
        data = tomllib.load(file)

    monitors = data.get("monitors")

    if not monitors:
        raise ValueError(f"В файле {path} нет ни одной секции [[monitors]]")

    if not isinstance(monitors, list):
        raise ValueError(
            f"В файле {path} ключ 'monitors' должен быть массивом таблиц [[monitors]]"
        )

    # isinstance сужает тип только до list[Unknown]; содержимое каждого элемента
    # проверяет Pydantic в model_validate.
    items = cast("list[object]", monitors)

    return [MonitorConfig.model_validate(item) for item in items]
