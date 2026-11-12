from typing import Any

import pytest
from pydantic import ValidationError

from pulse.tasks.settings import WorkerSettings

GOOD: dict[str, Any] = {
    "broker_url": "redis://:x@127.0.0.1:6379/1",
    "database_url": "postgresql+psycopg://pulse_app:x@127.0.0.1/pulse",
}


def settings(**overrides: Any) -> WorkerSettings:
    return WorkerSettings(**{**GOOD, **overrides})


def test_webhook_is_optional() -> None:
    assert settings().notify_webhook_url is None


def test_secrets_are_not_printed() -> None:
    text = repr(settings(notify_webhook_url="https://hooks.example/SECRET"))

    assert "SECRET" not in text
    assert "6379" not in text


def test_broker_must_be_redis() -> None:
    with pytest.raises(ValidationError, match="redis://"):
        settings(broker_url="amqp://guest@localhost//")


def test_database_must_be_the_async_driver() -> None:
    with pytest.raises(ValidationError, match="postgresql\\+psycopg"):
        settings(database_url="postgresql://x@localhost/pulse")


def test_error_does_not_leak_the_value() -> None:
    with pytest.raises(ValidationError) as info:
        settings(broker_url="amqp://guest:TOPSECRET@localhost//")

    assert "TOPSECRET" not in str(info.value)
