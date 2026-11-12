import asyncio
import logging

from celery import shared_task

from pulse.domain.reports import DailyDigest
from pulse.repositories.checks import CheckRepository
from pulse.repositories.monitors import MonitorRepository
from pulse.services.daily_digest import DailyDigestService
from pulse.tasks.names import DAILY_DIGEST
from pulse.tasks.resources import open_resources
from pulse.tasks.settings import get_worker_settings

logger = logging.getLogger("pulse.tasks")


async def _build_digest() -> DailyDigest:
    async with (
        open_resources(get_worker_settings()) as resources,
        resources.session_factory() as session,
    ):
        service = DailyDigestService(
            MonitorRepository(session), CheckRepository(session)
        )
        return await service.build()


def format_digest(digest: DailyDigest) -> str:
    uptime = (
        "нет проверок" if digest.uptime_percent is None else f"{digest.uptime_percent}%"
    )
    since = f"{digest.since:%Y-%m-%d %H:%M}"
    lines = [
        f"Сводка за сутки с {since} UTC: мониторов {digest.monitors},"
        f" проверок {digest.total}, аптайм {uptime}"
    ]
    lines += [
        f"  {line.uptime_percent:>7.3f}%  {line.url} ({line.ok}/{line.total})"
        for line in digest.worst
    ]
    return "\n".join(lines)


@shared_task(name=DAILY_DIGEST)
def daily_digest() -> str:
    """Ночная сводка. Запускается расписанием (Celery beat), а не пользователем.

    В реальности сводку отправили бы письмом; здесь она пишется в лог воркера.
    """
    text = format_digest(asyncio.run(_build_digest()))
    logger.info("%s", text)
    return text
