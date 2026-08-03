"""Observable facts emitted while a project is being developed."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


class ExecutionEventType(StrEnum):
    REACT_LOOP_ENTERED = "REACT_LOOP_ENTERED"
    REACT_LOOP_EXITED = "REACT_LOOP_EXITED"
    RUNTIME_CONTEXT_UPDATED = "RUNTIME_CONTEXT_UPDATED"
    PLAN_APPROVAL_REQUESTED = "PLAN_APPROVAL_REQUESTED"
    PLAN_APPROVED = "PLAN_APPROVED"
    PLAN_REJECTED = "PLAN_REJECTED"


class RuntimeContextChangeReason(StrEnum):
    CONVERSATION_STARTED = "CONVERSATION_STARTED"
    PLAN_APPROVAL_REQUESTED = "PLAN_APPROVAL_REQUESTED"
    PLAN_APPROVED = "PLAN_APPROVED"
    PLAN_REJECTED = "PLAN_REJECTED"


class ProjectOwnerTaskType(StrEnum):
    USER_INPUT = "USER_INPUT"
    EXECUTION_RESULT = "EXECUTION_RESULT"


@dataclass(frozen=True, slots=True)
class ProjectOwnerTask:
    type: ProjectOwnerTaskType
    content: str


@dataclass(frozen=True, slots=True)
class ExecutionEvent:
    """One event fact before or after Timeline persistence enrichment."""

    triage_id: str
    event_type: ExecutionEventType
    payload: dict[str, object] = field(default_factory=dict)
    react_loop_id: str | None = None
    message_id: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    event_id: int | None = None

    def __post_init__(self) -> None:
        if not self.triage_id.strip():
            raise ValueError("triage_id must not be empty")
        if self.event_id is not None and self.event_id <= 0:
            raise ValueError("event_id must be positive")
