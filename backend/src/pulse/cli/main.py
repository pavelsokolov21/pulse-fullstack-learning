import asyncio
import json
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

import httpx
import typer

from pulse.checks.models import CheckResult
from pulse.checks.runner import check_many
from pulse.cli.loader import load_monitors
from pulse.core.settings import Settings

app = typer.Typer()

EXIT_CHECK_FAILED = 1
EXIT_CONFIG_ERROR = 2


def create_client() -> httpx.AsyncClient:
    return httpx.AsyncClient()


async def run_checks(
    urls: Sequence[str], settings: Settings, client: httpx.AsyncClient
) -> list[CheckResult]:
    result = await check_many(
        client,
        urls,
        concurrency=settings.concurrency,
        retries=settings.retries,
        retry_delay=settings.retry_delay,
    )

    return result


_TABLE_HEADER = ("STATUS", "CODE", "MS", "URL", "ERROR")


def format_table(results: Sequence[CheckResult]) -> str:
    """Текстовая таблица: по строке на результат, колонки выровнены по ширине."""
    rows = [
        (
            "OK" if r.ok else "FAIL",
            "-" if r.status_code is None else str(r.status_code),
            f"{r.latency_ms:.0f}",
            r.url,
            r.error or "",
        )
        for r in results
    ]
    widths = [
        max(len(cell) for cell in column)
        for column in zip(_TABLE_HEADER, *rows, strict=True)
    ]

    # Последнюю колонку не добиваем пробелами: хвостовые пробелы мусор в выводе.
    def line(cells: tuple[str, ...]) -> str:
        padded = [
            cell.ljust(width) for cell, width in zip(cells[:-1], widths, strict=False)
        ]
        return "  ".join([*padded, cells[-1]]).rstrip()

    return "\n".join([line(_TABLE_HEADER), *(line(row) for row in rows)])


def format_json(results: Sequence[CheckResult]) -> str:
    """JSON-массив результатов; `ok` берётся из свойства, его нет среди полей."""
    return json.dumps(
        [{**asdict(r), "ok": r.ok} for r in results],
        ensure_ascii=False,
        indent=2,
    )


async def _check_monitors(urls: Sequence[str], settings: Settings) -> list[CheckResult]:
    # `async with` закрывает клиент и его соединения даже при исключении.
    async with create_client() as client:
        return await run_checks(urls, settings, client)


@app.callback()
def main() -> None:
    """Pulse: монитор доступности сайтов."""


@app.command()
def check(
    config: Annotated[
        Path,
        typer.Argument(
            exists=True, dir_okay=False, readable=True, help="TOML-файл с мониторами"
        ),
    ],
    as_json: Annotated[
        bool, typer.Option("--json", help="Вывести результат в JSON")
    ] = False,
) -> None:
    """Проверяет все мониторы из файла.

    Коды выхода: 0 все доступны, 1 есть недоступные, 2 ошибка конфигурации.
    """
    try:
        monitors = load_monitors(config)
        settings = Settings()
    except ValueError as exc:
        # TOMLDecodeError и ValidationError наследуются от ValueError.
        typer.echo(f"Ошибка конфигурации: {exc}", err=True)
        raise typer.Exit(EXIT_CONFIG_ERROR) from exc

    results = asyncio.run(_check_monitors([m.url for m in monitors], settings))

    typer.echo(format_json(results) if as_json else format_table(results))

    if not all(r.ok for r in results):
        raise typer.Exit(EXIT_CHECK_FAILED)
