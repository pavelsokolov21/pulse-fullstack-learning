from collections.abc import Awaitable, Callable

from pulse.repositories.incidents import IncidentRepository


class IncidentService:
    """Операции над инцидентами.

    `before_write` вызывается между чтением счётчика и его записью. В боевом коде его
    нет (`None`); тест подставляет паузу, чтобы удерживать блокировку строки и
    показать, что второй вызов ждёт, а не пишет поверх.
    """

    def __init__(
        self,
        repository: IncidentRepository,
        before_write: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._repository = repository
        self._before_write = before_write

    async def register_failed_check(self, incident_id: int) -> int:
        """Учитывает ещё одну неудачную проверку; возвращает новый счётчик.

        Прочитать, прибавить, записать в одной транзакции. Чтение берёт блокировку
        строки (`FOR UPDATE`), поэтому параллельный вызов ждёт на чтении, пока мы
        не сделаем `commit()`, и потом видит уже новое значение: прирост не теряется.
        При исключении выход из сессии откатывает транзакцию и снимает блокировку.
        """
        incident = await self._repository.get_for_update(incident_id)
        new_value = incident.failed_checks + 1
        if self._before_write is not None:
            await self._before_write()
        await self._repository.set_failed_checks(incident_id, new_value)
        await self._repository.commit()
        return new_value

    async def register_failed_check_optimistic(self, incident_id: int) -> int:
        """То же без блокировки: запись удаётся, только если строку не меняли.

        Читает счётчик с версией и пишет `WHERE version = <прочитанная>`. Если за это
        время кто-то успел записать, бросает `ConcurrentUpdateError`: повтор целой
        операции (с новой сессией) за вызывающим кодом, см. `services/retry.py`.
        """
        incident = await self._repository.get(incident_id)
        new_value = incident.failed_checks + 1
        if self._before_write is not None:
            await self._before_write()
        await self._repository.set_failed_checks_if_version(
            incident_id, new_value, incident.version
        )
        await self._repository.commit()
        return new_value
