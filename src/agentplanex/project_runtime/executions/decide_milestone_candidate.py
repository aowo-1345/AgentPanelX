"""Project Runtime execution for the Owner's Candidate decision."""

from typing import Literal

from pydantic import Field

from agentplanex.domains.project_runtime_state import ProjectRuntimeState
from agentplanex.project_owner_agent.contracts import (
    AgentExit,
    AgentExitStatus,
    ToolExecutionResult,
)
from agentplanex.project_owner_agent.tools import (
    NonBlankText,
    ToolArgumentsModel,
    ToolDefinition,
    ToolIdentifier,
)
from agentplanex.project_runtime.executions.base import (
    ProjectExecution,
    project_execution,
)
from agentplanex.services.delivery.contracts import DeliveryError
from agentplanex.services.delivery.models import CandidateIdentity

DECIDE_MILESTONE_CANDIDATE_TOOL_NAME = "decide_milestone_candidate"
DECIDE_MILESTONE_CANDIDATE_DESCRIPTION = (
    "Accept or reject the exact current Milestone Candidate after inspecting its "
    "fixed Git evidence and any delegated review. Accept integrates it and records "
    "Milestone completion; reject preserves it for audit and leaves the Milestone "
    "unfinished."
)


class DecideMilestoneCandidateArguments(ToolArgumentsModel):
    snapshot_id: ToolIdentifier
    run_id: ToolIdentifier
    milestone_key: ToolIdentifier
    candidate_commit_sha: ToolIdentifier
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
                expected=CandidateIdentity(
                    snapshot_id=arguments.snapshot_id,
                    run_id=arguments.run_id,
                    milestone_key=arguments.milestone_key,
                    candidate_commit_sha=arguments.candidate_commit_sha,
                ),
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
            "snapshot_id": result.identity.snapshot_id,
            "run_id": result.identity.run_id,
            "milestone_key": result.identity.milestone_key,
            "candidate_commit_sha": result.identity.candidate_commit_sha,
            "result_snapshot_id": result.result_snapshot_id,
            "next_milestone_key": result.next_milestone_key,
            "completed": result.completed,
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
