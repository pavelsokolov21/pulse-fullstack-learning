import asyncio
import time
from collections.abc import Sequence

import httpx

from pulse.checks.models import CheckResult
from pulse.decorators.validation import validate_range
from pulse.utils.normalize_url import normalize_url


async def check_url(client: httpx.AsyncClient, url: str) -> CheckResult:
    """Проверяет один URL.

    Сетевые сбои (таймаут, отказ соединения, DNS) не бросают исключение, а
    попадают в `CheckResult.error`. Мусорный URL, который нельзя даже
    отправить, бросает `ValueError`: это ошибка данных, а не «сайт недоступен».
    """
    target = normalize_url(url)

    start = time.perf_counter()
    status_code: int | None = None
    error: str | None = None

    try:
        response = await client.get(target, follow_redirects=True)
        status_code = response.status_code
    except httpx.InvalidURL as exc:
        raise ValueError(str(exc)) from exc
    except httpx.HTTPError as exc:
        error = f"Network Error: {type(exc).__name__}"

    latency_ms = (time.perf_counter() - start) * 1000

    return CheckResult(
        url=url,
        status_code=status_code,
        latency_ms=latency_ms,
        error=error,
    )


@validate_range(argument_name="concurrency", min_value=1, integer=True)
@validate_range(argument_name="retries", min_value=0, integer=True)
@validate_range(argument_name="retry_delay", min_value=0)
async def check_many(
    client: httpx.AsyncClient,
    urls: Sequence[str],
    *,
    concurrency: int = 10,
    retries: int = 2,
    retry_delay: float = 0.5,
) -> list[CheckResult]:
    semaphore = asyncio.Semaphore(concurrency)

    async def worker(url: str) -> CheckResult:
        attempt = 0

        while True:
            async with semaphore:
                try:
                    result = await check_url(client, url)
                except ValueError as exc:
                    return CheckResult(
                        url=url,
                        status_code=None,
                        latency_ms=0.0,
                        error=f"Invalid URL: {exc}",
                    )

            if result.status_code is not None or attempt == retries:
                return result

            attempt += 1
            await asyncio.sleep(retry_delay)

    async with asyncio.TaskGroup() as tg:
        tasks = [tg.create_task(worker(url)) for url in urls]

    return [task.result() for task in tasks]
