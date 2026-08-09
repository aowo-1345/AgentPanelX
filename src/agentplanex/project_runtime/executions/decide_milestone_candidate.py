"""Project Runtime execution for the Owner's Candidate decision."""

from typing import Literal

from agentplanex.domains import (
    AgentExit,
    AgentExitStatus,
    ProjectRuntimeContext,
    ToolArguments,
    ToolExecutionResult,
)
from agentplanex.project_owner_agent.tools import DECIDE_MILESTONE_CANDIDATE_TOOL
from agentplanex.project_runtime.executions.base import (
    ProjectExecution,
    project_execution,
)
from agentplanex.services.delivery import DeliveryError


@project_execution(DECIDE_MILESTONE_CANDIDATE_TOOL)
class DecideMilestoneCandidateExecution(ProjectExecution):
    """Apply a typed accept or reject decision to the exact current Candidate."""

    def execute(
        self,
        context: ProjectRuntimeContext,
        arguments: ToolArguments,
    ) -> ToolExecutionResult:
        try:
            decision, reason = self._arguments(arguments)
            result = self.dependencies.delivery.decide_milestone_candidate(
                context,
                decision=decision,
                reason=reason,
            )
        except DeliveryError as error:
            return ToolExecutionResult(output={"ok": False, "error": str(error)})

        output: dict[str, object] = {
            "ok": True,
            "decision": result.decision,
            "triage_id": result.context.triage_id,
            "status": result.context.status,
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

    @staticmethod
    def _arguments(arguments: ToolArguments) -> tuple[Literal["accept", "reject"], str]:
        if set(arguments) != {"decision", "reason"}:
            raise DeliveryError(
                "decide_milestone_candidate requires only decision and reason arguments"
            )
        decision = arguments.get("decision")
        reason = arguments.get("reason")
        if decision not in {"accept", "reject"}:
            raise DeliveryError("Candidate decision must be accept or reject")
        if not isinstance(reason, str):
            raise DeliveryError("Candidate decision reason must be a string")
        return decision, reason
