import json
import logging
from dataclasses import dataclass
from datetime import datetime

from redis.exceptions import RedisError

from pulse.domain.reports import MonitorReport, ReportWindow
from pulse.repositories.redis_client import RedisClient

logger = logging.getLogger("pulse.cache")

# Счётчик версий живёт дольше любой записи отчёта: иначе после его истечения версия
# сбросилась бы в 0 и могла совпасть с версией давно устаревшей записи.
VERSION_TTL_SECONDS = 7 * 24 * 3600


@dataclass(frozen=True)
class CacheLookup:
    """Что нашлось в кеше и под какой версией надо сохранять свежий расчёт.

    `report` пуст при промахе. `version` пуст, если Redis недоступен: тогда и
    сохранять нечего (запись без версии нельзя будет проверить на устаревание).
    """

    report: MonitorReport | None
    version: int | None


class ReportCache:
    """Кеш отчётов монитора: cache-aside с версионной инвалидацией.

    Приложение само ходит в базу при промахе и само кладёт результат (cache-aside,
    «ленивая загрузка»), в отличие от write-through, где запись идёт через кеш.

    Инвалидация без гонок. Простое «удалить ключ после записи» уязвимо: читатель
    прочитал старые данные из базы, писатель записал и удалил ключ, читатель положил
    СТАРОЕ значение обратно, и оно живёт до TTL. Поэтому у каждого монитора есть
    счётчик версии. Читатель берёт версию ДО обращения к базе и кладёт отчёт вместе с
    ней. Запись меняет данные и поднимает версию; запись со старой версией уже не
    совпадёт с текущей и будет проигнорирована. Версия и значение читаются одним
    запросом к Redis (pipeline), так что попадание стоит один круг по сети.

    Кеш это оптимизация, а не источник данных: любая ошибка Redis означает «кеша нет»
    (промах, пропущенная запись), запрос при этом не падает.
    """

    def __init__(self, client: RedisClient, ttl_seconds: int) -> None:
        if ttl_seconds < 1:
            raise ValueError("ttl_seconds должен быть не меньше 1")
        self._client = client
        self._ttl = ttl_seconds

    @staticmethod
    def _version_key(monitor_id: int) -> str:
        return f"pulse:report:ver:{monitor_id}"

    @staticmethod
    def _key(monitor_id: int, window: ReportWindow) -> str:
        return f"pulse:report:{monitor_id}:{window.value}"

    async def get(self, monitor_id: int, window: ReportWindow) -> CacheLookup:
        try:
            async with self._client.pipeline(transaction=False) as pipe:
                pipe.get(self._version_key(monitor_id))
                pipe.get(self._key(monitor_id, window))
                raw_version, raw_report = await pipe.execute()
        except (RedisError, OSError):
            logger.warning("Redis недоступен, отчёт читается без кеша", exc_info=True)
            return CacheLookup(None, None)

        version = int(raw_version) if raw_version is not None else 0
        if raw_report is None:
            return CacheLookup(None, version)
        report = self._decode(raw_report, version)
        return CacheLookup(report, version)

    async def put(
        self, monitor_id: int, window: ReportWindow, report: MonitorReport, version: int
    ) -> None:
        payload = json.dumps({"version": version, "report": _to_json(report)})
        try:
            await self._client.set(self._key(monitor_id, window), payload, ex=self._ttl)
        except (RedisError, OSError):
            logger.warning("Redis недоступен, отчёт не закеширован", exc_info=True)

    async def invalidate(self, monitor_id: int) -> None:
        """Все отчёты монитора становятся устаревшими. Вызывать ПОСЛЕ коммита записи.

        До коммита нельзя: читатель, пришедший между сбросом и коммитом, прочитал бы из
        базы ещё старые данные и закешировал их под новой версией.
        """
        try:
            async with self._client.pipeline(transaction=True) as pipe:
                pipe.incr(self._version_key(monitor_id))
                pipe.expire(self._version_key(monitor_id), VERSION_TTL_SECONDS)
                await pipe.execute()
        except (RedisError, OSError):
            # Запись уже в базе, так что вызывающему падать незачем: устаревший отчёт
            # проживёт не дольше TTL.
            logger.error("Не удалось сбросить кеш отчётов монитора", exc_info=True)

    @staticmethod
    def _decode(raw: str, current_version: int) -> MonitorReport | None:
        try:
            data = json.loads(raw)
            if data["version"] != current_version:
                return None  # записан до последнего изменения данных
            return _from_json(data["report"])
        except (ValueError, KeyError, TypeError):
            # Мусор в ключе (чужая запись, старый формат) это промах, а не 500.
            logger.warning("Повреждённая запись кеша отчёта", exc_info=True)
            return None


def _to_json(report: MonitorReport) -> dict[str, object]:
    return {
        "monitor_id": report.monitor_id,
        "window": report.window.value,
        "total": report.total,
        "ok": report.ok,
        "uptime_percent": report.uptime_percent,
        "avg_latency_ms": report.avg_latency_ms,
        "p95_latency_ms": report.p95_latency_ms,
        "generated_at": report.generated_at.isoformat(),
    }


def _from_json(data: dict[str, object]) -> MonitorReport:
    return MonitorReport(
        monitor_id=int(str(data["monitor_id"])),
        window=ReportWindow(str(data["window"])),
        total=int(str(data["total"])),
        ok=int(str(data["ok"])),
        uptime_percent=_opt_float(data["uptime_percent"]),
        avg_latency_ms=_opt_float(data["avg_latency_ms"]),
        p95_latency_ms=_opt_float(data["p95_latency_ms"]),
        generated_at=datetime.fromisoformat(str(data["generated_at"])),
    )


def _opt_float(value: object) -> float | None:
    return None if value is None else float(str(value))
