from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from pulse.domain.errors import DuplicateUserError
from pulse.domain.users import Role, User
from pulse.repositories.database import Session, constraint_name
from pulse.repositories.models import UserModel


def _to_domain(model: UserModel) -> User:
    return User(model.id, model.email, model.password_hash, Role(model.role))


class UserRepository:
    """Пользователи в PostgreSQL.

    Email приходит уже нормализованным. Уникальность закрепляет UNIQUE
    `users_email_key`, поэтому «проверить, потом создать» не нужно: гонку двух
    одинаковых регистраций решает база.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    async def get(self, user_id: int) -> User | None:
        model = await self._session.get(UserModel, user_id)
        return None if model is None else _to_domain(model)

    async def find_by_email(self, email: str) -> User | None:
        model = await self._session.scalar(
            select(UserModel).where(UserModel.email == email)
        )
        return None if model is None else _to_domain(model)

    async def create(self, *, email: str, password_hash: str, role: Role) -> User:
        model = UserModel(email=email, password_hash=password_hash, role=role.value)
        try:
            # SAVEPOINT: при нарушении откатывается только эта вставка, а сессия
            # остаётся рабочей (без него вся транзакция стала бы непригодной).
            async with self._session.begin_nested():
                self._session.add(model)
        except IntegrityError as exc:
            if constraint_name(exc) == "users_email_key":
                raise DuplicateUserError from exc
            raise
        return _to_domain(model)
