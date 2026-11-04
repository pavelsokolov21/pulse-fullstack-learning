import uuid
from datetime import datetime

from sqlalchemy import select, update

from pulse.domain.refresh_tokens import RefreshToken
from pulse.repositories.database import Session
from pulse.repositories.models import RefreshTokenModel


def _to_domain(model: RefreshTokenModel) -> RefreshToken:
    return RefreshToken(
        id=model.id,
        user_id=model.user_id,
        family_id=model.family_id.hex,
        token_hash=model.token_hash,
        expires_at=model.expires_at,
        revoked_at=model.revoked_at,
    )


class RefreshTokenRepository:
    """Refresh-токены в PostgreSQL.

    Ищет по хешу токена: сырое значение знает только клиент. Просроченные и
    отозванные записи не удаляются (нужны, чтобы заметить повторное
    использование); их чистит периодическая задача (отложено, см. README).
    `family_id` в домене это строка (hex от uuid4), в базе тип uuid.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    async def add(
        self, *, user_id: int, family_id: str, token_hash: str, expires_at: datetime
    ) -> RefreshToken:
        model = RefreshTokenModel(
            user_id=user_id,
            family_id=uuid.UUID(family_id),
            token_hash=token_hash,
            expires_at=expires_at,
        )
        self._session.add(model)
        await self._session.flush()
        return _to_domain(model)

    async def find_by_hash(self, token_hash: str) -> RefreshToken | None:
        model = await self._session.scalar(
            select(RefreshTokenModel)
            .where(RefreshTokenModel.token_hash == token_hash)
            # Свежие данные из базы, а не копия из сессии: токен могли отозвать
            # UPDATE-ом, который не трогает уже загруженные объекты.
            .execution_options(populate_existing=True)
        )
        return None if model is None else _to_domain(model)

    async def revoke(self, token_id: int, at: datetime) -> bool:
        """Отзывает токен. `False`, если он уже был отозван (в том числе только что
        параллельным запросом).

        Условие `revoked_at IS NULL` делает проверку и отзыв одной атомарной
        операцией: из двух одновременных обновлений одного токена выиграет одно.
        """
        revoked = await self._session.scalar(
            update(RefreshTokenModel)
            .where(
                RefreshTokenModel.id == token_id,
                RefreshTokenModel.revoked_at.is_(None),
            )
            .values(revoked_at=at)
            .returning(RefreshTokenModel.id)
        )
        return revoked is not None

    async def revoke_family(self, family_id: str, at: datetime) -> None:
        """Отзывает действующие токены цепочки; прежнее время отзыва не трогает."""
        await self._session.execute(
            update(RefreshTokenModel)
            .where(
                RefreshTokenModel.family_id == uuid.UUID(family_id),
                RefreshTokenModel.revoked_at.is_(None),
            )
            .values(revoked_at=at)
        )

    async def commit(self) -> None:
        """Фиксирует транзакцию немедленно.

        Нужно, когда запрос после записи всё равно завершится ошибкой (401): иначе
        откат запроса отменил бы запись. Так сохраняется отзыв цепочки при повторном
        использовании токена, ради которого ответ и есть отказ.
        """
        await self._session.commit()

    async def list_family(self, family_id: str) -> list[RefreshToken]:
        result = await self._session.scalars(
            select(RefreshTokenModel)
            .where(RefreshTokenModel.family_id == uuid.UUID(family_id))
            .order_by(RefreshTokenModel.id)
            .execution_options(populate_existing=True)
        )
        return [_to_domain(t) for t in result]
