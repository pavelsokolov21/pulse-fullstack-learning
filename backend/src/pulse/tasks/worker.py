"""Точка входа воркера и планировщика.

    celery -A pulse.tasks.worker worker --loglevel=INFO --concurrency=2
    celery -A pulse.tasks.worker beat --loglevel=INFO

Планировщик (beat) только кладёт задачи в очередь по расписанию, выполняет их воркер.
Планировщик должен быть ОДИН: два экземпляра ставили бы каждую задачу дважды.
"""

from celery.schedules import crontab

from pulse.tasks.app import create_celery_app
from pulse.tasks.names import DAILY_DIGEST
from pulse.tasks.settings import get_worker_settings

app = create_celery_app(get_worker_settings().broker_url.get_secret_value())
# Модули с задачами импортируются при старте воркера: так задачи регистрируются.
app.conf.imports = ("pulse.tasks.notifications", "pulse.tasks.scheduled")
app.conf.beat_schedule = {
    "daily-digest": {
        "task": DAILY_DIGEST,
        "schedule": crontab(hour=3, minute=0),  # 03:00 UTC каждую ночь
    },
}
