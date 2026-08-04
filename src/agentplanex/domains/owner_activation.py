"""Durable inputs that start one Project Owner ReAct Loop."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from agentplanex.domains.execution_event import ProjectOwnerTaskType


class OwnerActivationStatus(StrEnum):
    """Lifecycle of one durable Project Owner activation."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class OwnerActivation:
    """A durable pointer to one external input that needs Owner processing."""

    activation_id: str
    triage_id: str
    task_type: ProjectOwnerTaskType
    message_id: str
    status: OwnerActivationStatus = OwnerActivationStatus.PENDING
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    finished_at: datetime | None = None
    failure: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("activation_id", "triage_id", "message_id"):
            if not getattr(self, field_name).strip():
                raise ValueError(f"{field_name} must not be empty")
        if self.status is OwnerActivationStatus.PENDING:
            if any(
                value is not None
                for value in (self.started_at, self.finished_at, self.failure)
            ):
                raise ValueError("Pending activation cannot have lifecycle results")
            return
        if self.started_at is None:
            raise ValueError("Started activation must have started_at")
        if self.status is OwnerActivationStatus.RUNNING:
            if self.finished_at is not None or self.failure is not None:
                raise ValueError("Running activation cannot have a terminal result")
            return
        if self.finished_at is None:
            raise ValueError("Finished activation must have finished_at")
        if self.status is OwnerActivationStatus.FAILED:
            if self.failure is None or not self.failure.strip():
                raise ValueError("Failed activation must contain a failure")
        elif self.failure is not None:
            raise ValueError("Only failed activations may contain a failure")
