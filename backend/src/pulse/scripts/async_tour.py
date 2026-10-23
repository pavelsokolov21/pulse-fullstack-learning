import asyncio
from asyncio import gather
from time import perf_counter

from pulse.checks.models import CheckResult
from pulse.utils.timer import timed


async def probe(host: str, delay: float, *, fail: bool = False) -> CheckResult:
    await asyncio.sleep(delay)
    if fail:
        raise RuntimeError(f"bug in {host}")
    return CheckResult(
        url=f"https://{host}/", status_code=200, latency_ms=delay * 1000, error=None
    )


def get_probes():
    return [
        probe("test1", 1),
        probe("test2", 5, fail=True),
        probe("test3", 3),
        probe("test4", 2),
        probe("test5", 6),
    ]


async def run_task_1() -> list[CheckResult | BaseException]:
    res = await gather(*get_probes(), return_exceptions=True)
    print(res)
    return res


async def run_test_2() -> list[CheckResult | BaseException]:
    tasks = []

    try:
        async with asyncio.TaskGroup() as tg:
            for coro in get_probes():
                task = tg.create_task(coro)
                tasks.append(task)
    except* RuntimeError as eg:
        print(f"TaskGroup поймал исключения: {eg.exceptions}")

    res = []
    for t in tasks:
        if t.cancelled():
            res.append(asyncio.CancelledError("Task was cancelled by TaskGroup"))
        elif t.exception():
            res.append(t.exception())
        else:
            res.append(t.result())

    print("\nФинальный массив результатов:")
    for item in res:
        print(f"  - {item!r}")

    return res


async def run_task_3(deadline: float = 2.5) -> list[CheckResult]:
    tasks: list[asyncio.Task[CheckResult]] = []

    with timed() as t:
        try:
            async with asyncio.timeout(deadline):
                async with asyncio.TaskGroup() as tg:
                    for coro in get_probes():
                        tasks.append(tg.create_task(coro))
        except* TimeoutError:
            print(f"Дедлайн {deadline} с сработал")
        except* RuntimeError as eg:
            print(f"Баг в пробах: {[str(e) for e in eg.exceptions]}")

    # TaskGroup дожидается отменённых задач, поэтому к этому месту все done()
    ok = [
        task.result()
        for task in tasks
        if not task.cancelled() and task.exception() is None
    ]

    print(f"Успели {len(ok)} из {len(tasks)}: {[r.url for r in ok]}")
    print(f"Общее время: {t.elapsed_ms / 1000:.2f} с")
    return ok


PROBE_SPECS = [
    ("test1", 1, False),
    ("test2", 5, True),
    ("test3", 3, False),
    ("test4", 2, False),
    ("test5", 6, False),
]


async def run_task_4() -> None:
    tasks = [
        asyncio.create_task(probe(host, delay, fail=fail), name=host)
        for host, delay, fail in PROBE_SPECS
    ]
    start = perf_counter()

    async for fut in asyncio.as_completed(tasks):
        host = fut.get_name()
        try:
            res = await fut
            line = f"{res.url}  ok"
        except RuntimeError as exc:
            line = f"{host}  FAILED  {exc}"

        print(f"{perf_counter() - start:5.2f} с  {line}")


type Job = tuple[str, float, bool]  # (host, delay, fail)


async def worker(name: str, queue: asyncio.Queue[Job], start: float) -> None:
    while True:
        host, delay, fail = await queue.get()
        try:
            url = await probe(host, delay, fail=fail)
            line = f"{url} ok"
        except RuntimeError as exc:
            line = f"{host} FAILED {exc}"
        finally:
            queue.task_done()

        print(f"{perf_counter() - start:4.2f} с  {name}  {line}")


async def run_task_5() -> None:
    queue: asyncio.Queue[Job] = asyncio.Queue()
    jobs: list[Job] = [
        ("h1", 0.3, False),
        ("h2", 0.1, False),
        ("h3", 0.2, True),
        ("h4", 0.1, False),
        ("h5", 0.3, False),
        ("h6", 0.1, False),
    ]
    for job in jobs:
        queue.put_nowait(job)

    start = perf_counter()
    workers = [asyncio.create_task(worker(f"w{i}", queue, start)) for i in range(3)]

    await queue.join()  # ждём, пока обработаны ВСЕ элементы

    for w in workers:  # воркеры в while True сами не завершатся
        w.cancel()
    await asyncio.gather(*workers, return_exceptions=True)

    print(f"Всего {perf_counter() - start:.2f} с")


if __name__ == "__main__":
    asyncio.run(run_task_5())
