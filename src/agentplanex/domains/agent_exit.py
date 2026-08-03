"""Agent Loop exit result."""

from dataclasses import dataclass
from enum import StrEnum


class AgentExitStatus(StrEnum):
    """Supported reasons for leaving an Agent Loop."""

    REPLY_TO_HUMAN = "ReplyToHuman"
    PLAN_APPROVAL_REQUESTED = "PlanApprovalRequested"
    MILESTONE_RUN_QUEUED = "MilestoneRunQueued"
    AGENT_TASK_QUEUED = "AgentTaskQueued"
    REPEATED_FORMAT_ERROR = "RepeatedFormatError"
    STEP_LIMIT_EXCEEDED = "StepLimitExceeded"
    UNHANDLED_EXCEPTION = "UnhandledException"


@dataclass(frozen=True, slots=True)
class AgentExit:
    """Typed result returned when an Agent Loop stops."""

    status: AgentExitStatus
    content: str
