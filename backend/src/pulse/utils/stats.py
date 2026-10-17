from collections.abc import Sequence
from urllib.parse import urlparse

from pulse.checks.models import CheckResult, HostMeta, HostStats
from pulse.decorators.validation import validate_range
from pulse.utils.percentile import percentile


def _with_response(results: Sequence[CheckResult]) -> list[CheckResult]:
    """Проверки, где пришёл ответ.

    У сетевого сбоя задержка это время до таймаута, а не скорость сайта, поэтому
    `slowest` и `p95` считаются только по проверкам с ответом. Ответ с ошибкой
    (например, 503) входит: его задержка настоящая.
    """
    return [res for res in results if res.status_code is not None]


@validate_range(argument_name="n", min_value=0, integer=True)
def slowest(results: Sequence[CheckResult], n: int = 3) -> list[CheckResult]:
    """`n` самых медленных проверок с ответом, по убыванию задержки.

    При равной задержке сохраняется порядок входа.
    """
    return sorted(
        _with_response(results), key=lambda res: res.latency_ms, reverse=True
    )[:n]


def group_by_host(results: Sequence[CheckResult]) -> dict[str, list[CheckResult]]:
    groups: dict[str, list[CheckResult]] = {}

    for res in results:
        hostname = urlparse(res.url).hostname

        # URL монитора нормализуется при создании, поэтому хост есть всегда.
        # Результат без хоста это повреждённые данные: не прячем их в общую
        # группу, а падаем.
        if hostname is None:
            raise ValueError(f"В URL результата нет хоста: {res.url!r}")

        groups.setdefault(hostname, []).append(res)

    return groups


def get_host_meta(results: Sequence[CheckResult]) -> HostMeta:
    total = len(results)
    failed = sum(1 for res in results if not res.ok)

    return HostMeta(total=total, failed=failed)


def _p95(results: Sequence[CheckResult]) -> float | None:
    """p95 задержки по проверкам с ответом, `None`, если ответов нет совсем."""
    latencies = [res.latency_ms for res in _with_response(results)]

    return percentile(latencies, 95) if latencies else None


def summarize(results: Sequence[CheckResult]) -> list[HostStats]:
    """Статистика по каждому хосту.

    Первыми идут хосты с наибольшим **количеством** сбоев (а не долей): список
    отвечает на вопрос «где больше всего проблем». При равенстве сохраняется
    порядок первого появления хоста.
    """
    stats_list: list[HostStats] = []

    for hostname, results_by_host in group_by_host(results).items():
        host_meta = get_host_meta(results_by_host)

        stats_list.append(
            HostStats(
                host=hostname,
                total=host_meta.total,
                failed=host_meta.failed,
                # Группа не бывает пустой, поэтому total > 0.
                failure_rate=host_meta.failed / host_meta.total,
                p95=_p95(results_by_host),
            )
        )

    stats_list.sort(key=lambda stats: stats.failed, reverse=True)

    return stats_list


def failure_rate_by_host(results: Sequence[CheckResult]) -> dict[str, float]:
    return {stats.host: stats.failure_rate for stats in summarize(results)}


def p95_by_host(results: Sequence[CheckResult]) -> dict[str, float | None]:
    return {stats.host: stats.p95 for stats in summarize(results)}
