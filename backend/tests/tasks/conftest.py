from typing import Any

import pytest

import pulse.tasks.notifications as task_module


def _ignore(*args: Any, **kwargs: Any) -> None:
    return None


@pytest.fixture(autouse=True)
def _no_journal_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Задачи в тестах не пишут в Redis; журнал проверяют отдельные тесты."""
    monkeypatch.setattr(task_module, "_journal", _ignore)
