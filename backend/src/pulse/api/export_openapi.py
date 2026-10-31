"""Пишет OpenAPI-схему в файл, не поднимая сервер.

    uv run python -m pulse.api.export_openapi ../frontend/openapi.json

Схема строится из объекта приложения, поэтому запуск не нужен. Секрет JWT на
схему не влияет, подставляется заглушка: так команда работает без окружения.
"""

import argparse
import json
import os
from pathlib import Path
from typing import Any

PLACEHOLDER_SECRET = "openapi-export-placeholder-not-a-real-secret"


def build_schema() -> dict[str, Any]:
    # `pulse.api.main` на импорте создаёт глобальное `app` и требует секрет, поэтому
    # заглушка ставится до импорта, и сам импорт отложен до этого места.
    os.environ.setdefault("PULSE_API_JWT_SECRET", PLACEHOLDER_SECRET)
    from pulse.api.main import create_app

    return create_app().openapi()


def render_schema(schema: dict[str, Any]) -> str:
    # Стабильный вид (отступы, кириллица как есть, перевод строки в конце):
    # diff схемы в git показывает только настоящие изменения.
    return json.dumps(schema, indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Записать OpenAPI-схему в файл")
    parser.add_argument("output", type=Path, help="куда писать, например openapi.json")
    args = parser.parse_args(argv)

    args.output.write_text(render_schema(build_schema()), encoding="utf-8")


if __name__ == "__main__":
    main()
