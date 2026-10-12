import time

import httpx

from pulse.checks.models import CheckResult


async def check_url(client: httpx.AsyncClient, url: str) -> CheckResult:
    start = time.perf_counter()
    status_code: int | None = None
    error: str | None = None

    try:
        response = await client.get(url, follow_redirects=True)
        status_code = response.status_code
    except httpx.HTTPError as exc:
        error = f"Network Error: {type(exc).__name__}"

    latency_ms = (time.perf_counter() - start) * 1000

    return CheckResult(
        url=url,
        status_code=status_code,
        latency_ms=latency_ms,
        error=error,
    )
