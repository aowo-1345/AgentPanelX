"""Observable facts emitted while a project is being developed."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


class ExecutionEventType(StrEnum):
    REACT_LOOP_ENTERED = "REACT_LOOP_ENTERED"
    REACT_LOOP_EXITED = "REACT_LOOP_EXITED"
    CONTEXT_COMPACTION_STARTED = "CONTEXT_COMPACTION_STARTED"
    CONTEXT_COMPACTION_COMPLETED = "CONTEXT_COMPACTION_COMPLETED"
    CONTEXT_COMPACTION_FAILED = "CONTEXT_COMPACTION_FAILED"
    RUNTIME_CONTEXT_UPDATED = "RUNTIME_CONTEXT_UPDATED"
    AGENT_INVOCATION_STARTED = "AGENT_INVOCATION_STARTED"
    AGENT_INVOCATION_COMPLETED = "AGENT_INVOCATION_COMPLETED"
    AGENT_INVOCATION_FAILED = "AGENT_INVOCATION_FAILED"
    PLAN_APPROVAL_REQUESTED = "PLAN_APPROVAL_REQUESTED"
    PLAN_APPROVED = "PLAN_APPROVED"
    PLAN_REJECTED = "PLAN_REJECTED"
    MILESTONES_UPDATED = "MILESTONES_UPDATED"
    FIRST_RUN_APPROVAL_REQUESTED = "FIRST_RUN_APPROVAL_REQUESTED"
    MILESTONE_RUN_QUEUED = "MILESTONE_RUN_QUEUED"
    STAGE_RUN_STARTED = "STAGE_RUN_STARTED"
    STAGE_RUN_SUCCEEDED = "STAGE_RUN_SUCCEEDED"
    STAGE_RUN_FAILED = "STAGE_RUN_FAILED"
    CANDIDATE_READY = "CANDIDATE_READY"
    CANDIDATE_ACCEPTED = "CANDIDATE_ACCEPTED"
    CANDIDATE_REJECTED = "CANDIDATE_REJECTED"
    TRIAGE_DEVELOPMENT_COMPLETED = "TRIAGE_DEVELOPMENT_COMPLETED"


class RuntimeContextChangeReason(StrEnum):
    FEATURE_BEGUN = "FEATURE_BEGUN"
    CONVERSATION_STARTED = "CONVERSATION_STARTED"
    USER_INTERVENTION_REQUIRED = "USER_INTERVENTION_REQUIRED"
    PLAN_APPROVAL_REQUESTED = "PLAN_APPROVAL_REQUESTED"
    PLAN_APPROVED = "PLAN_APPROVED"
    PLAN_REJECTED = "PLAN_REJECTED"
    MILESTONES_UPDATED = "MILESTONES_UPDATED"
    FIRST_RUN_APPROVAL_REQUESTED = "FIRST_RUN_APPROVAL_REQUESTED"
    FIRST_RUN_STARTED = "FIRST_RUN_STARTED"
    MILESTONE_RUN_QUEUED = "MILESTONE_RUN_QUEUED"
    STAGE_RUN_STARTED = "STAGE_RUN_STARTED"
    STAGE_RUN_SUCCEEDED = "STAGE_RUN_SUCCEEDED"
    STAGE_RUN_FAILED = "STAGE_RUN_FAILED"
    CANDIDATE_READY = "CANDIDATE_READY"
    CANDIDATE_ACCEPTED = "CANDIDATE_ACCEPTED"
    CANDIDATE_REJECTED = "CANDIDATE_REJECTED"
    TRIAGE_DEVELOPMENT_COMPLETED = "TRIAGE_DEVELOPMENT_COMPLETED"


class ProjectOwnerTaskType(StrEnum):
    USER_INPUT = "USER_INPUT"
    PLAN_DECISION = "PLAN_DECISION"
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
