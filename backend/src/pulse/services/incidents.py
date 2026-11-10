from collections.abc import Awaitable, Callable

from pulse.repositories.incidents import IncidentRepository


class IncidentService:
    """Операции над инцидентами.

    `before_write` вызывается между чтением счётчика и его записью. В боевом коде его
    нет (`None`); тест подставляет барьер, чтобы два вызова гарантированно прочитали
    значение до того, как любой из них запишет.
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

        Прочитать, прибавить, записать: между чтением и записью другой запрос успевает
        сделать то же самое, и один из двух приростов пропадает.
        """
        # NOTE: намеренная гонка (lost update), чинится на неделе 5
        incident = await self._repository.get(incident_id)
        new_value = incident.failed_checks + 1
        if self._before_write is not None:
            await self._before_write()
        await self._repository.set_failed_checks(incident_id, new_value)
        await self._repository.commit()
        return new_value
