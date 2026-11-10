from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from pulse.domain.concurrency_demo import EventKind, Strategy

MIN_HOLD_MS = 100
MAX_HOLD_MS = 2000
DEFAULT_HOLD_MS = 400


class ConcurrencyRunRequest(BaseModel):
    strategy: Strategy
    hold_ms: Annotated[
        int,
        Field(
            ge=MIN_HOLD_MS,
            le=MAX_HOLD_MS,
            description="Сколько каждый вызов держит строку между чтением и записью",
        ),
    ] = DEFAULT_HOLD_MS


class TraceEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    call: int
    attempt: int
    at_ms: float
    kind: EventKind
    value: int | None


class CallResultRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    call: int
    returned: int | None
    attempts: int
    error: str | None


class ConcurrencyRunRead(BaseModel):
    strategy: Strategy
    hold_ms: int
    initial: int
    expected: int
    final: int
    final_version: int
    elapsed_ms: float
    lost_update: bool
    events: list[TraceEventRead]
    calls: list[CallResultRead]
