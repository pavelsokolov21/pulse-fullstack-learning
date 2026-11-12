"""Направление зависимостей между слоями: api → services → repositories → domain.

Тест читает импорты из исходников (ast), не запуская код, и падает, как только
нижний слой начинает зависеть от верхнего.
"""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).parents[1] / "src" / "pulse"

# Слой -> пакеты pulse, от которых ему зависеть нельзя.
# `tasks` (Celery) внешний слой, как `api`: он вызывает сервисы, а они его не знают.
FORBIDDEN: dict[str, set[str]] = {
    "core": {"api", "services", "repositories", "domain", "checks", "cli", "tasks"},
    "domain": {"api", "services", "repositories", "checks", "cli", "tasks"},
    "repositories": {"api", "services", "checks", "cli", "tasks"},
    "services": {"api", "cli", "tasks"},
    "checks": {"api", "services", "repositories", "cli", "tasks"},
    "api": {"cli"},
    "cli": {"api", "services", "repositories", "tasks"},
    "tasks": {"api", "cli"},
}


def imported_modules(path: Path) -> set[str]:
    """Полные имена модулей из всех абсолютных импортов файла."""
    modules: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
    return modules


def imported_packages(path: Path) -> set[str]:
    """Первые компоненты `pulse.<пакет>` из импортов файла."""
    packages: set[str] = set()
    for name in imported_modules(path):
        parts = name.split(".")
        if parts[0] == "pulse" and len(parts) > 1:
            packages.add(parts[1])
    return packages


def source_files(layer: str) -> list[Path]:
    return sorted((SRC / layer).rglob("*.py"))


def test_every_layer_directory_exists() -> None:
    # Защита от опечатки в таблице: пустая проверка всегда зелёная.
    for layer in FORBIDDEN:
        assert source_files(layer), layer


@pytest.mark.parametrize("layer", sorted(FORBIDDEN))
def test_layer_does_not_import_forbidden_layers(layer: str) -> None:
    violations = [
        f"{path.relative_to(SRC)} -> pulse.{package}"
        for path in source_files(layer)
        for package in sorted(imported_packages(path) & FORBIDDEN[layer])
    ]

    assert violations == []


def test_checker_detects_a_violation(tmp_path: Path) -> None:
    bad = tmp_path / "bad.py"
    bad.write_text("from pulse.api.main import app\nimport pulse.services.x\n")

    assert imported_packages(bad) == {"api", "services"}


# Драйвер, ORM и клиент Redis знает только слой репозиториев: сервисы и API работают
# с доменными объектами и не могут зависеть от того, чем хранятся данные.
DATABASE_PACKAGES = {"sqlalchemy", "psycopg", "asyncpg", "alembic", "redis"}


def test_database_libraries_are_only_used_in_repositories() -> None:
    outside = [
        str(path.relative_to(SRC))
        for path in sorted(SRC.rglob("*.py"))
        if path.relative_to(SRC).parts[0] != "repositories"
        and any(m.split(".")[0] in DATABASE_PACKAGES for m in imported_modules(path))
    ]

    assert outside == []


def test_database_check_detects_a_violation(tmp_path: Path) -> None:
    bad = tmp_path / "bad.py"
    bad.write_text("from sqlalchemy import select\nimport psycopg.errors\n")

    used = {m.split(".")[0] for m in imported_modules(bad)}
    assert used & DATABASE_PACKAGES == {"sqlalchemy", "psycopg"}


def test_redis_is_a_checked_storage_library() -> None:
    assert "redis" in DATABASE_PACKAGES


def test_fastapi_is_only_used_in_api_layer() -> None:
    # Остальные слои не должны знать про HTTP-фреймворк.
    outside = [
        str(path.relative_to(SRC))
        for path in sorted(SRC.rglob("*.py"))
        if path.relative_to(SRC).parts[0] != "api"
        and any(m.split(".")[0] == "fastapi" for m in imported_modules(path))
    ]

    assert outside == []


def test_celery_is_only_used_in_tasks_layer() -> None:
    # Сервисы не знают про Celery: их логику проверяют без брокера и воркера.
    outside = [
        str(path.relative_to(SRC))
        for path in sorted(SRC.rglob("*.py"))
        if path.relative_to(SRC).parts[0] != "tasks"
        and any(m.split(".")[0] in {"celery", "kombu"} for m in imported_modules(path))
    ]

    assert outside == []


def test_every_lower_layer_forbids_depending_on_tasks() -> None:
    # Сейчас ни один нижний слой не импортирует `tasks`, поэтому проверка выше
    # зелёная и без запрета. Запрет должен быть записан явно, иначе первый же
    # импорт сервисом задачи Celery пройдёт молча.
    lower = {"core", "domain", "repositories", "services", "checks", "cli"}

    assert {layer for layer in lower if "tasks" not in FORBIDDEN[layer]} == set()
