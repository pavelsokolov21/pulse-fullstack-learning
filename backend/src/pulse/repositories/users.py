from itertools import count

from pulse.domain.users import Role, User


class UserRepository:
    """Пользователи в памяти (до PostgreSQL в фазе 2).

    Email приходит уже нормализованным; уникальность проверяет сервис, а в базе
    её закрепит UNIQUE-ограничение. Не потокобезопасен: вызывается из одного
    потока event loop.
    """

    def __init__(self) -> None:
        self._users: dict[int, User] = {}
        self._ids = count(1)

    def get(self, user_id: int) -> User | None:
        return self._users.get(user_id)

    def find_by_email(self, email: str) -> User | None:
        return next((u for u in self._users.values() if u.email == email), None)

    def create(self, *, email: str, password_hash: str, role: Role) -> User:
        user = User(next(self._ids), email, password_hash, role)
        self._users[user.id] = user
        return user
