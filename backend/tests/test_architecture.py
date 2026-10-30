"""Направление зависимостей между слоями: api → services → repositories → domain.

Тест читает импорты из исходников (ast), не запуская код, и падает, как только
нижний слой начинает зависеть от верхнего.
"""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).parents[1] / "src" / "pulse"

# Слой -> пакеты pulse, от которых ему зависеть нельзя.
FORBIDDEN: dict[str, set[str]] = {
    "core": {"api", "services", "repositories", "domain", "checks", "cli"},
    "domain": {"api", "services", "repositories", "checks", "cli"},
    "repositories": {"api", "services", "checks", "cli"},
    "services": {"api", "cli"},
    "checks": {"api", "services", "repositories", "cli"},
    "api": {"cli"},
    "cli": {"api", "services", "repositories"},
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


def test_fastapi_is_only_used_in_api_layer() -> None:
    # Остальные слои не должны знать про HTTP-фреймворк.
    outside = [
        str(path.relative_to(SRC))
        for path in sorted(SRC.rglob("*.py"))
        if path.relative_to(SRC).parts[0] != "api"
        and any(m.split(".")[0] == "fastapi" for m in imported_modules(path))
    ]

    assert outside == []
