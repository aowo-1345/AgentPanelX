"""Project Runtime execution for requesting Plan approval."""

from agentplanex.domains.project_runtime_state import ProjectRuntimeState
from agentplanex.project_owner_agent.contracts import (
    AgentExit,
    AgentExitStatus,
    ToolExecutionResult,
)
from agentplanex.project_owner_agent.tools import NoToolArguments, ToolDefinition
from agentplanex.project_runtime.executions.base import (
    ProjectExecution,
    project_execution,
)
from agentplanex.services.planning.contracts import PlanningError

REQUEST_PLAN_APPROVAL_TOOL_NAME = "request_plan_approval"
REQUEST_PLAN_APPROVAL_DESCRIPTION = (
    "Submit the exact current architecture.md, requirements.md, and roadmap.md "
    "as one canonical Plan for explicit user approval. This never approves the "
    "Plan on the Owner's behalf. Runtime invokes the Plan Hard Gate only while "
    "rolling delivery is IN_PROGRESS."
)
REQUEST_PLAN_APPROVAL_TOOL = ToolDefinition(
    name=REQUEST_PLAN_APPROVAL_TOOL_NAME,
    description=REQUEST_PLAN_APPROVAL_DESCRIPTION,
    arguments_type=NoToolArguments,
)


@project_execution(REQUEST_PLAN_APPROVAL_TOOL)
class RequestPlanApprovalExecution(ProjectExecution[NoToolArguments]):
    """Request approval for the current project specification documents."""

    def execute(
        self,
        _context: ProjectRuntimeState,
        arguments: NoToolArguments,
    ) -> ToolExecutionResult:
        try:
            requested = self.dependencies.planning.request_plan_approval()
        except PlanningError as error:
            return ToolExecutionResult(
                output={
                    "ok": False,
                    "error": str(error),
                }
            )

        output = {
            "ok": True,
            "accepted": requested.accepted,
            "triage_id": requested.state.triage_id,
            "status": requested.state.status,
            "pending_action": requested.state.pending_action,
            "subject_digest": requested.subject_digest,
            "hard_gate_invoked": requested.review is not None,
            "review": None,
        }
        review = requested.review
        if review is not None:
            output["review"] = {
                "decision": review.decision,
                "summary": review.summary,
                "required_changes": list(review.required_changes),
                "artifact": {
                    "uri": review.audit_artifact.uri,
                    "project_relative_path": (
                        review.audit_artifact.project_relative_path
                    ),
                    "media_type": review.audit_artifact.media_type,
                    "size": review.audit_artifact.size,
                    "sha256": review.audit_artifact.sha256,
                },
            }
        if not requested.accepted:
            return ToolExecutionResult(output=output)
        return ToolExecutionResult(
            output=output,
            exit=AgentExit(
                status=AgentExitStatus.PLAN_APPROVAL_REQUESTED,
                content=(
                    "The exact current Plan is waiting for explicit user approval."
                ),
            ),
        )
