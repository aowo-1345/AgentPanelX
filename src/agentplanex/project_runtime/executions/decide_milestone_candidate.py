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
    "Accept, reject, or revise the exact current Milestone Candidate after inspecting "
    "its fixed Git evidence and any delegated review. Accept integrates it and records "
    "Milestone completion; reject leaves it unfinished for a later Run; revise keeps "
    "the Candidate unfinished and queues the same Executor session with the feedback "
    "for a new Candidate."
)


class DecideMilestoneCandidateArguments(ToolArgumentsModel):
    snapshot_id: ToolIdentifier
    run_id: ToolIdentifier
    milestone_key: ToolIdentifier
    candidate_commit_sha: ToolIdentifier
    decision: Literal["accept", "reject", "revise"] = Field(
        description="Whether to accept, reject, or request a revision of the exact Candidate."
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
    """Apply a typed decision to the exact current Candidate."""

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
        if result.revision_stage_run_id is not None:
            output["revision_stage_run_id"] = result.revision_stage_run_id
            output["follow_up"] = (
                "Revision queued. Drive Delivery to reactivate the Executor, then inspect "
                "the new Candidate before deciding again."
            )
        if result.decision == "reject":
            output["follow_up"] = (
                "Review the rejection reason and adjust the Milestone tasks when "
                "needed before retrying."
            )
        if result.decision == "reject" and result.state.status == "BLOCKED":
            # Persist the provider-required observation before terminating the
            # Owner loop.  Raising here would leave the model's function_call
            # without a matching function_call_output in message history.
            return ToolExecutionResult(
                output=output,
                exit=AgentExit(
                    status=AgentExitStatus.REPEATED_CANDIDATE_REJECTION,
                    content=AgentExitStatus.REPEATED_CANDIDATE_REJECTION.value,
                ),
            )
        if result.decision == "revise" and result.state.status == "BLOCKED":
            output["follow_up"] = (
                "Repeated Candidate revisions are blocked. Inspect the revision chain "
                "and resolve the underlying issue before retrying the Run."
            )
            return ToolExecutionResult(
                output=output,
                exit=AgentExit(
                    status=AgentExitStatus.REPEATED_CANDIDATE_REVISION,
                    content=AgentExitStatus.REPEATED_CANDIDATE_REVISION.value,
                ),
            )
        if not result.completed:
            return ToolExecutionResult(output=output)
        return ToolExecutionResult(
            output=output,
            exit=AgentExit(
                status=AgentExitStatus.TRIAGE_DEVELOPMENT_COMPLETED,
                content="All Milestones are complete and the project is now DONE.",
            ),
        )
