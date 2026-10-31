import asyncio

from pwdlib import PasswordHash

DEFAULT_MAX_CONCURRENT = 4


class PasswordHasher:
    """Асинхронная обёртка над `PasswordHash` (argon2id по умолчанию).

    Хеширование занимает десятки миллисекунд и сотни мегабайт памяти на запрос,
    поэтому:
    - работа уходит в поток (`to_thread`), иначе она блокирует event loop;
    - одновременно хешируется не больше `max_concurrent` паролей: иначе поток
      запросов на логин съест память (argon2 берёт 64 МиБ на хеш) и пул потоков.
    """

    def __init__(
        self,
        password_hash: PasswordHash | None = None,
        *,
        max_concurrent: int = DEFAULT_MAX_CONCURRENT,
    ) -> None:
        if max_concurrent < 1:
            raise ValueError("max_concurrent должен быть не меньше 1")
        self._password_hash = password_hash or PasswordHash.recommended()
        self._slots = asyncio.Semaphore(max_concurrent)
        self._dummy_hash: str | None = None

    async def hash(self, password: str) -> str:
        async with self._slots:
            return await asyncio.to_thread(self._password_hash.hash, password)

    async def verify(self, password: str, hashed: str) -> bool:
        async with self._slots:
            return await asyncio.to_thread(self._password_hash.verify, password, hashed)

    async def verify_dummy(self, password: str) -> None:
        """Тратит столько же времени, сколько `verify`, и ничего не проверяет.

        Нужен при входе несуществующего пользователя: без него ответ приходит
        на десятки миллисекунд быстрее, и по времени видно, какие email есть.
        """
        if self._dummy_hash is None:
            self._dummy_hash = await self.hash("dummy-password-for-timing")
        await self.verify(password, self._dummy_hash)
