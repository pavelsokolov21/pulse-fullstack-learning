from datetime import timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from pulse.domain.errors import DuplicateMonitorError, MonitorNotFoundError
from pulse.domain.monitors import Monitor, MonitorWithOwner
from pulse.domain.pagination import Page
from pulse.repositories.database import Session, constraint_name
from pulse.repositories.models import MonitorModel, UserModel


def _to_domain(model: MonitorModel) -> Monitor:
    return Monitor(
        model.id,
        model.owner_id,
        model.url,
        timedelta(seconds=model.interval_seconds),
        model.timeout_seconds,
    )


def _to_listed(model: MonitorModel, owner_email: str) -> MonitorWithOwner:
    return MonitorWithOwner(
        model.id,
        model.owner_id,
        model.url,
        timedelta(seconds=model.interval_seconds),
        model.timeout_seconds,
        owner_email,
    )


class MonitorRepository:
    """Мониторы в PostgreSQL.

    Значения принимаются уже проверенными: валидация живёт на границе (схемы API),
    а база страхует её своими CHECK. Уникальность (владелец, URL) решает UNIQUE
    `monitors_owner_url_key`.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    async def list_page(
        self, *, owner_id: int | None, limit: int, offset: int
    ) -> Page[MonitorWithOwner]:
        """Страница мониторов с email владельца; `owner_id=None` значит «всех».

        Фильтр по владельцу здесь, а не в сервисе: в базе это WHERE. Порядок по id
        нужен, чтобы страницы не перемешивались между запросами (без ORDER BY
        Postgres порядок не гарантирует).
        """
        owned = [] if owner_id is None else [MonitorModel.owner_id == owner_id]
        rows = await self._session.execute(
            select(MonitorModel, UserModel.email)
            # Владелец это внешний ключ NOT NULL, поэтому обычный JOIN ничего не теряет.
            .join(UserModel, UserModel.id == MonitorModel.owner_id)
            .where(*owned)
            .order_by(MonitorModel.id)
            .limit(limit)
            .offset(offset)
        )
        total = await self._session.scalar(
            select(func.count()).select_from(MonitorModel).where(*owned)
        )
        return Page([_to_listed(m, email) for m, email in rows], total or 0)

    async def get(self, monitor_id: int) -> Monitor:
        return _to_domain(await self._get_model(monitor_id))

    async def find_by_url(self, owner_id: int, url: str) -> Monitor | None:
        """Монитор владельца с точно таким URL (URL уже нормализован) или `None`."""
        model = await self._session.scalar(
            select(MonitorModel).where(
                MonitorModel.owner_id == owner_id, MonitorModel.url == url
            )
        )
        return None if model is None else _to_domain(model)

    async def create(
        self, *, owner_id: int, url: str, interval: timedelta, timeout: float
    ) -> Monitor:
        model = MonitorModel(
            owner_id=owner_id,
            url=url,
            interval_seconds=int(interval.total_seconds()),
            timeout_seconds=timeout,
        )
        try:
            async with self._session.begin_nested():
                self._session.add(model)
        except IntegrityError as exc:
            await self._raise_if_duplicate(exc, owner_id, url)
            raise
        return _to_domain(model)

    async def update(
        self,
        monitor_id: int,
        *,
        url: str | None = None,
        interval: timedelta | None = None,
        timeout: float | None = None,
    ) -> Monitor:
        """Меняет только переданные поля; `None` означает «не менять»."""
        model = await self._get_model(monitor_id)
        owner_id = model.owner_id  # после отката SAVEPOINT атрибуты «протухают»
        try:
            # Присваивания внутри блока: запрос уходит при выходе из него, то есть
            # внутри SAVEPOINT, и нарушение не ломает всю транзакцию.
            async with self._session.begin_nested():
                if url is not None:
                    model.url = url
                if interval is not None:
                    model.interval_seconds = int(interval.total_seconds())
                if timeout is not None:
                    model.timeout_seconds = timeout
        except IntegrityError as exc:
            if url is not None:
                await self._raise_if_duplicate(exc, owner_id, url)
            raise
        return _to_domain(model)

    async def delete(self, monitor_id: int) -> None:
        """Удаляет монитор; его проверки уходят каскадом (ON DELETE CASCADE)."""
        deleted = await self._session.scalar(
            delete(MonitorModel)
            .where(MonitorModel.id == monitor_id)
            .returning(MonitorModel.id)
        )
        if deleted is None:
            raise MonitorNotFoundError(monitor_id)

    async def _get_model(self, monitor_id: int) -> MonitorModel:
        model = await self._session.get(MonitorModel, monitor_id)
        if model is None:
            raise MonitorNotFoundError(monitor_id)
        return model

    async def _raise_if_duplicate(
        self, error: IntegrityError, owner_id: int, url: str
    ) -> None:
        if constraint_name(error) != "monitors_owner_url_key":
            return
        existing = await self.find_by_url(owner_id, url)
        if existing is not None:
            raise DuplicateMonitorError(url, existing.id) from error
