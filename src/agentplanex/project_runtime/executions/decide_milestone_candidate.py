"""Project Runtime execution for the Owner's Candidate decision."""

from typing import Literal

from pydantic import Field

from agentplanex.domains import (
    AgentExit,
    AgentExitStatus,
    ProjectRuntimeState,
    ToolExecutionResult,
)
from agentplanex.project_owner_agent.tools import (
    NonBlankText,
    ToolArgumentsModel,
    ToolDefinition,
)
from agentplanex.project_runtime.executions.base import (
    ProjectExecution,
    project_execution,
)
from agentplanex.services.delivery import DeliveryError

DECIDE_MILESTONE_CANDIDATE_TOOL_NAME = "decide_milestone_candidate"
DECIDE_MILESTONE_CANDIDATE_DESCRIPTION = (
    "Accept or reject the exact current Milestone Candidate after inspecting its "
    "fixed Git evidence and any delegated review. Accept integrates it and records "
    "Milestone completion; reject preserves it for audit and leaves the Milestone "
    "unfinished."
)


class DecideMilestoneCandidateArguments(ToolArgumentsModel):
    decision: Literal["accept", "reject"] = Field(
        description="Whether to accept or reject the exact current Candidate."
    )
    reason: NonBlankText = Field(
        description="Concise evidence-based reason for the decision."
    )


DECIDE_MILESTONE_CANDIDATE_TOOL = ToolDefinition(
    name=DECIDE_MILESTONE_CANDIDATE_TOOL_NAME,
    description=DECIDE_MILESTONE_CANDIDATE_DESCRIPTION,
    arguments_type=DecideMilestoneCandidateArguments,
)


@project_execution(DECIDE_MILESTONE_CANDIDATE_TOOL)
class DecideMilestoneCandidateExecution(
    ProjectExecution[DecideMilestoneCandidateArguments]
):
    """Apply a typed accept or reject decision to the exact current Candidate."""

    def execute(
        self,
        _context: ProjectRuntimeState,
        arguments: DecideMilestoneCandidateArguments,
    ) -> ToolExecutionResult:
        try:
            result = self.dependencies.delivery.decide_milestone_candidate(
                decision=arguments.decision,
                reason=arguments.reason,
            )
        except DeliveryError as error:
            return ToolExecutionResult(output={"ok": False, "error": str(error)})

        output: dict[str, object] = {
            "ok": True,
            "decision": result.decision,
            "triage_id": result.state.triage_id,
            "status": result.state.status,
            "milestone_key": result.milestone_key,
            "candidate_commit_sha": result.candidate_commit_sha,
            "next_milestone_key": result.next_milestone_key,
            "completed": result.completed,
        }
        if result.snapshot is not None:
            output["snapshot"] = {
                "snapshot_id": result.snapshot.snapshot_id,
                "previous_snapshot_id": result.snapshot.previous_snapshot_id,
            }
        if not result.completed:
            return ToolExecutionResult(output=output)
        return ToolExecutionResult(
            output=output,
            exit=AgentExit(
                status=AgentExitStatus.TRIAGE_DEVELOPMENT_COMPLETED,
                content="All Milestones are complete and the project is now DONE.",
            ),
        )
