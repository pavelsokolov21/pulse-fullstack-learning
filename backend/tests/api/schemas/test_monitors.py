from datetime import timedelta

import pytest
from pydantic import ValidationError

from pulse.api.schemas.monitors import MonitorCreate, MonitorRead, MonitorUpdate
from pulse.domain.monitors import Monitor, MonitorConfig


def test_create_and_config_validate_identically() -> None:
    data = {"url": "Example.com/a ", "interval": "30s", "timeout": 3}

    create = MonitorCreate.model_validate(data).model_dump()
    config = MonitorConfig.model_validate(data).model_dump()

    assert create == config


@pytest.mark.parametrize("interval", [0, -5, True, float("nan"), "0s"])
def test_create_rejects_invalid_interval_in_any_form(interval: object) -> None:
    with pytest.raises(ValidationError):
        MonitorCreate.model_validate({"url": "a.test", "interval": interval})


@pytest.mark.parametrize("field", ["url", "interval", "timeout"])
def test_update_rejects_explicit_null(field: str) -> None:
    with pytest.raises(ValidationError):
        MonitorUpdate.model_validate({field: None})


def test_update_tracks_which_fields_were_set() -> None:
    update = MonitorUpdate.model_validate({"timeout": 5})

    assert update.model_dump(exclude_unset=True) == {"timeout": 5.0}


def test_read_is_built_from_monitor_without_revalidation() -> None:
    # Выходная модель «глупая»: значение из хранилища берётся как есть.
    monitor = Monitor(1, 1, "not-normalized-on-purpose", timedelta(minutes=5), 3.0)

    read = MonitorRead.model_validate(monitor)

    assert read.url == "not-normalized-on-purpose"


def test_read_serializes_interval_as_seconds() -> None:
    read = MonitorRead(
        id=1,
        owner_id=1,
        url="https://a.test",
        interval=timedelta(minutes=5),
        timeout=3.0,
    )

    assert read.model_dump(mode="json")["interval"] == 300.0


def test_read_is_frozen() -> None:
    read = MonitorRead(
        id=1,
        owner_id=1,
        url="https://a.test",
        interval=timedelta(seconds=1),
        timeout=1.0,
    )

    with pytest.raises(ValidationError):
        read.timeout = 2.0
