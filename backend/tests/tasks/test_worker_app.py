"""Настройки Celery и расписание: ошибки здесь проявляются только в бою."""

from celery.schedules import crontab

from pulse.tasks.app import VISIBILITY_TIMEOUT_SECONDS, create_celery_app
from pulse.tasks.backoff import CAP_SECONDS
from pulse.tasks.names import DAILY_DIGEST, NOTIFY_CHECK_FAILED
from pulse.tasks.worker import app as worker_app

BROKER = "redis://:x@127.0.0.1:6379/1"


def test_messages_are_json_only() -> None:
    conf = create_celery_app(BROKER).conf

    # pickle из очереди это выполнение чужого кода.
    assert conf.task_serializer == "json"
    assert list(conf.accept_content) == ["json"]


def test_message_is_acknowledged_after_the_task_not_before() -> None:
    conf = create_celery_app(BROKER).conf

    assert conf.task_acks_late is True
    assert conf.task_reject_on_worker_lost is True
    assert conf.worker_prefetch_multiplier == 1


def test_visibility_timeout_outlives_the_longest_retry_pause() -> None:
    # Иначе задача с отложенным повтором вернётся в очередь раньше срока и выполнится
    # дважды.
    options = create_celery_app(BROKER).conf.broker_transport_options

    assert options["visibility_timeout"] == VISIBILITY_TIMEOUT_SECONDS
    assert VISIBILITY_TIMEOUT_SECONDS > CAP_SECONDS


def test_hard_time_limit_is_longer_than_the_soft_one() -> None:
    conf = create_celery_app(BROKER).conf

    assert conf.task_soft_time_limit < conf.task_time_limit < VISIBILITY_TIMEOUT_SECONDS


def test_nightly_digest_is_scheduled_at_three_utc() -> None:
    entry = worker_app.conf.beat_schedule["daily-digest"]

    assert entry["task"] == DAILY_DIGEST
    assert entry["schedule"] == crontab(hour=3, minute=0)
    assert worker_app.conf.timezone == "UTC"


def test_worker_registers_both_tasks() -> None:
    worker_app.loader.import_default_modules()  # то же делает воркер при старте

    assert {NOTIFY_CHECK_FAILED, DAILY_DIGEST} <= set(worker_app.tasks)
