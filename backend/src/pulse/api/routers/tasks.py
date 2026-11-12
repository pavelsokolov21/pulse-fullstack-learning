from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from pulse.api.deps import TaskJournalDep, get_current_user, require_writer
from pulse.api.errors import ErrorResponse, ValidationErrorResponse
from pulse.api.schemas.tasks import TaskEventRead

router = APIRouter(
    prefix="/tasks",
    tags=["tasks"],
    dependencies=[Depends(get_current_user), Depends(require_writer)],
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_403_FORBIDDEN: {"model": ErrorResponse},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ValidationErrorResponse},
    },
)

DEFAULT_LIMIT = 100
MAX_LIMIT = 300
Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT)]


@router.get("/events")
async def list_task_events(
    journal: TaskJournalDep, limit: Limit = DEFAULT_LIMIT
) -> list[TaskEventRead]:
    """Последние события фоновых задач, новые первыми.

    Журнал общий для всех пользователей и хранит только номера проверок и
    причины сбоев, поэтому читать его могут те же роли, что запускают проверки.
    """
    events = await journal.recent(limit)
    return [TaskEventRead.model_validate(e) for e in events]
