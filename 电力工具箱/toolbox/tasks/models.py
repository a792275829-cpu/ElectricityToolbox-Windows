from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from typing import Any
from toolbox_cancellation import CancellationToken, TaskCancelled


class TaskState(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    CANCELLING = "cancelling"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskEventKind(str, Enum):
    STATE = "state"
    LOG = "log"
    PROGRESS = "progress"
    RESULT = "result"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class TaskEvent:
    task_id: str
    kind: TaskEventKind
    message: str = ""
    progress: float | None = None
    payload: Any = None
    created_at: datetime = field(default_factory=datetime.now)


@dataclass(frozen=True, slots=True)
class TaskSnapshot:
    task_id: str
    name: str
    state: TaskState = TaskState.CREATED
    progress: float | None = None
    message: str = ""
    result: Any = None
    error: BaseException | None = None
    cancellable: bool = True

    def transition(self, state: TaskState, **changes: Any) -> "TaskSnapshot":
        return replace(self, state=state, **changes)
