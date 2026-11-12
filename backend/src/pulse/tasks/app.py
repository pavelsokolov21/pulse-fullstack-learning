from celery import Celery

# Запас на самое долгое ожидание: ETA и countdown у повторов (до CAP_SECONDS) должны
# быть короче. Иначе брокер решит, что воркер потерял сообщение, и выдаст его другому:
# задача выполнится дважды.
VISIBILITY_TIMEOUT_SECONDS = 3600


def create_celery_app(broker_url: str) -> Celery:
    """Приложение Celery с общими настройками для воркера и для отправителя (API).

    Настройки здесь, а не в коде задач: сообщение публикует один процесс, а читает
    другой, и сериализатор и очередь у них обязаны совпадать.
    """
    app = Celery("pulse", broker=broker_url)
    app.conf.update(
        # JSON вместо pickle: pickle из очереди это выполнение чужого кода.
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        timezone="UTC",
        enable_utc=True,
        task_default_queue="pulse",
        # Подтверждать сообщение ПОСЛЕ выполнения, а не при получении. Упал воркер на
        # середине: сообщение не подтверждено и будет выдано заново.
        task_acks_late=True,
        # Воркер убит (OOM, kill -9): вернуть задачу в очередь, а не потерять.
        task_reject_on_worker_lost=True,
        # По одному сообщению за раз: иначе воркер забирает пачку впрок, и долгая
        # задача держит за собой взятые, но не начатые.
        worker_prefetch_multiplier=1,
        # Результаты нам не нужны (никто не ждёт ответа): без бэкенда и без записей.
        task_ignore_result=True,
        # Мягкий лимит бросает исключение внутри задачи (можно прибраться), жёсткий
        # убивает процесс. Оба короче visibility timeout.
        task_soft_time_limit=30,
        task_time_limit=60,
        # Брокер ещё не поднялся при старте воркера: ждать, а не падать.
        broker_connection_retry_on_startup=True,
        broker_transport_options={
            "visibility_timeout": VISIBILITY_TIMEOUT_SECONDS,
            "socket_connect_timeout": 2,
            "socket_timeout": 5,
        },
        # Публикация из API: не висеть, если брокер лежит.
        task_publish_retry_policy={
            "max_retries": 2,
            "interval_start": 0.0,
            "interval_step": 0.2,
            "interval_max": 0.5,
        },
    )
    return app
