"""Durable Owner work models owned by the Project Runtime Context."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


class ProjectOwnerTaskType(StrEnum):
    """The external input kinds accepted by the Project Owner."""

    USER_INPUT = "USER_INPUT"
    PLAN_DECISION = "PLAN_DECISION"
    EXECUTION_RESULT = "EXECUTION_RESULT"


@dataclass(frozen=True, slots=True)
class ProjectOwnerTask:
    """One external input submitted for durable Owner processing."""

    type: ProjectOwnerTaskType
    content: str


class OwnerActivationStatus(StrEnum):
    """Lifecycle of one durable Project Owner activation."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class OwnerActivationMode(StrEnum):
    """The driver selected to consume an activation.

    ``started_at`` remains the authoritative fact for whether that driver ever
    claimed work. A process interruption can terminalize accepted automatic work
    before its selected MODEL driver starts.
    """

    MODEL = "MODEL"
    TOOL = "TOOL"


@dataclass(frozen=True, slots=True)
class OwnerActivation:
    """A durable pointer to one external input that needs Owner processing."""

    activation_id: str
    triage_id: str
    task_type: ProjectOwnerTaskType
    message_id: str
    summary_id: str | None = None
    status: OwnerActivationStatus = OwnerActivationStatus.PENDING
    driver_mode: OwnerActivationMode | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    finished_at: datetime | None = None
    failure: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("activation_id", "triage_id", "message_id"):
            if not getattr(self, field_name).strip():
                raise ValueError(f"{field_name} must not be empty")
        if self.summary_id is not None and not self.summary_id.strip():
            raise ValueError("summary_id must not be empty")
        if self.status is OwnerActivationStatus.PENDING:
            if self.driver_mode is None:
                if any(
                    value is not None
                    for value in (self.started_at, self.finished_at, self.failure)
                ):
                    raise ValueError(
                        "New pending activation cannot have lifecycle results"
                    )
                return
            if self.driver_mode is not OwnerActivationMode.TOOL:
                raise ValueError("Only a Tool activation can wait for another step")
            if self.started_at is None:
                raise ValueError("Resumable Tool activation must have started_at")
            if self.finished_at is not None or self.failure is not None:
                raise ValueError("Resumable Tool activation cannot have a terminal result")
            return
        if self.driver_mode is None:
            raise ValueError("Started activation must have a driver mode")
        if self.status is OwnerActivationStatus.RUNNING:
            if self.started_at is None:
                raise ValueError("Running activation must have started_at")
            if self.finished_at is not None or self.failure is not None:
                raise ValueError("Running activation cannot have a terminal result")
            return
        if self.finished_at is None:
            raise ValueError("Finished activation must have finished_at")
        if (
            self.status is OwnerActivationStatus.COMPLETED
            and self.started_at is None
        ):
            raise ValueError("Completed activation must have started_at")
        if self.status is OwnerActivationStatus.FAILED:
            if self.failure is None or not self.failure.strip():
                raise ValueError("Failed activation must contain a failure")
        elif self.failure is not None:
            raise ValueError("Only failed activations may contain a failure")
