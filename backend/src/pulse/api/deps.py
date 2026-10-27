from typing import Annotated

from fastapi import Depends, Request

from pulse.monitors.store import MonitorStore


def get_store(request: Request) -> MonitorStore:
    store: MonitorStore = request.app.state.store
    return store


StoreDep = Annotated[MonitorStore, Depends(get_store)]
