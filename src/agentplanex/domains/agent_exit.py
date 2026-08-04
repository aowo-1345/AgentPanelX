"""Agent Loop exit result."""

from dataclasses import dataclass
from enum import StrEnum


class AgentExitStatus(StrEnum):
    """Supported reasons for leaving an Agent Loop."""

    REPLY_TO_HUMAN = "ReplyToHuman"
    PLAN_APPROVAL_REQUESTED = "PlanApprovalRequested"
    FIRST_RUN_APPROVAL_REQUESTED = "FirstRunApprovalRequested"
    MILESTONE_RUN_QUEUED = "MilestoneRunQueued"
    TRIAGE_DEVELOPMENT_COMPLETED = "TriageDevelopmentCompleted"
    AGENT_TASK_QUEUED = "AgentTaskQueued"
    MANUAL_DRIVE_FAILED = "ManualDriveFailed"
    REPEATED_FORMAT_ERROR = "RepeatedFormatError"
    STEP_LIMIT_EXCEEDED = "StepLimitExceeded"
    UNHANDLED_EXCEPTION = "UnhandledException"


@dataclass(frozen=True, slots=True)
class AgentExit:
    """Typed result returned when an Agent Loop stops."""

    status: AgentExitStatus
    content: str
