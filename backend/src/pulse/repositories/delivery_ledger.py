import logging

from redis.exceptions import RedisError

from pulse.domain.notifications import RetryableDeliveryError
from pulse.repositories.redis_client import RedisClient

logger = logging.getLogger("pulse.notifications")

# Сколько помним, что уведомление по проверке уже ушло. Дольше, чем живёт повтор
# задачи (минуты) и чем хранится сама проверка в учебных объёмах.
SENT_TTL_SECONDS = 7 * 24 * 3600


class RedisDeliveryLedger:
    """Журнал «уведомление по этой проверке отправлено» (идемпотентность задачи).

    Брокер гарантирует «минимум один раз»: задача может прийти повторно (воркер
    упал после отправки, но до подтверждения; дубль публикации). Без журнала
    пользователь получил бы два одинаковых сообщения. Журнал не делает доставку
    «ровно один раз»: между отправкой и `mark_sent` остаётся окно, в котором падение
    воркера даст дубль. Это сознательный выбор в пользу «лучше дважды, чем ни разу».
    """

    def __init__(self, client: RedisClient, prefix: str = "pulse:notify:sent") -> None:
        self._client = client
        self._prefix = prefix

    def _key(self, check_id: int) -> str:
        return f"{self._prefix}:{check_id}"

    async def was_sent(self, check_id: int) -> bool:
        try:
            return bool(await self._client.exists(self._key(check_id)))
        except (RedisError, OSError) as exc:
            # Не знаем, отправляли ли: молча отправить второй раз нельзя, а потерять
            # уведомление дороже. Пусть задача повторится, когда Redis вернётся.
            logger.warning("Журнал доставки недоступен", exc_info=True)
            raise RetryableDeliveryError("журнал доставки недоступен") from exc

    async def mark_sent(self, check_id: int) -> None:
        try:
            await self._client.set(self._key(check_id), "1", ex=SENT_TTL_SECONDS)
        except (RedisError, OSError):
            # Сообщение уже ушло. Повтор задачи только ради записи в журнал дал бы
            # дубль, поэтому здесь ошибку проглатываем и логируем.
            logger.error("Не удалось записать отправку в журнал", exc_info=True)
