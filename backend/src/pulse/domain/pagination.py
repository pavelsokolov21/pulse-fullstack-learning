from dataclasses import dataclass

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


@dataclass(frozen=True)
class Page[T]:
    """Одна страница списка и общее число записей (для «страница 2 из 7»)."""

    items: list[T]
    total: int
