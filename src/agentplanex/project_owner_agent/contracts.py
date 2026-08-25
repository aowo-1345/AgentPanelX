"""Call contracts exposed by the Project Owner Agent."""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

type Message = dict[str, Any]
type ToolArguments = dict[str, Any]
type ToolSchema = dict[str, Any]
type Action = dict[str, Any]
type ActionOutput = dict[str, Any]


class AgentExitStatus(StrEnum):
    """Supported reasons for leaving the Project Owner Agent loop."""

    REPLY_TO_HUMAN = "ReplyToHuman"
    PLAN_APPROVAL_REQUESTED = "PlanApprovalRequested"
    FIRST_RUN_APPROVAL_REQUESTED = "FirstRunApprovalRequested"
    BLOCKED_RUN_APPROVAL_REQUESTED = "BlockedRunApprovalRequested"
    MILESTONE_RUN_QUEUED = "MilestoneRunQueued"
    TRIAGE_DEVELOPMENT_COMPLETED = "TriageDevelopmentCompleted"
    AGENT_TASK_QUEUED = "AgentTaskQueued"
    MANUAL_DRIVE_FAILED = "ManualDriveFailed"
    REPEATED_FORMAT_ERROR = "RepeatedFormatError"
    REPEATED_CANDIDATE_REJECTION = "RepeatedCandidateRejection"
    STEP_LIMIT_EXCEEDED = "StepLimitExceeded"
    UNHANDLED_EXCEPTION = "UnhandledException"


@dataclass(frozen=True, slots=True)
class AgentExit:
    """Typed result returned when the Project Owner Agent loop stops."""

    status: AgentExitStatus
    content: str


@dataclass(frozen=True, slots=True)
class ToolExecutionResult:
    """One tool observation and an optional request to stop the Agent loop."""

    output: ActionOutput
    exit: AgentExit | None = None


type AgentToolExecutor = Callable[[Action], ToolExecutionResult]
