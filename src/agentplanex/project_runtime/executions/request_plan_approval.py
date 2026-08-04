"""Project Runtime execution for requesting Plan approval."""

from agentplanex.domains import (
    AgentExit,
    AgentExitStatus,
    ProjectRuntimeContext,
    ToolArguments,
    ToolExecutionResult,
)
from agentplanex.project_owner_agent.tools import REQUEST_PLAN_APPROVAL_TOOL
from agentplanex.project_runtime.executions.base import (
    ProjectExecution,
    project_execution,
)
from agentplanex.services.planning import PlanningError


@project_execution(REQUEST_PLAN_APPROVAL_TOOL)
class RequestPlanApprovalExecution(ProjectExecution):
    """Request approval for the current project specification documents."""

    def execute(
        self,
        context: ProjectRuntimeContext,
        arguments: ToolArguments,
    ) -> ToolExecutionResult:
        if arguments:
            return ToolExecutionResult(
                output={
                    "ok": False,
                    "error": "request_plan_approval does not accept arguments",
                }
            )

        try:
            requested = self.dependencies.planning.request_plan_approval(context)
        except PlanningError as error:
            return ToolExecutionResult(
                output={
                    "ok": False,
                    "error": str(error),
                }
            )

        review = requested.review
        output = {
            "ok": True,
            "accepted": requested.accepted,
            "triage_id": requested.context.triage_id,
            "status": requested.context.status,
            "pending_action": requested.context.pending_action,
            "review": {
                "decision": review.decision,
                "summary": review.summary,
                "required_changes": list(review.required_changes),
                "artifact": {
                    "uri": review.audit_artifact.uri,
                    "media_type": review.audit_artifact.media_type,
                    "size": review.audit_artifact.size,
                    "sha256": review.audit_artifact.sha256,
                },
            },
        }
        if not requested.accepted:
            return ToolExecutionResult(output=output)
        return ToolExecutionResult(
            output=output,
            exit=AgentExit(
                status=AgentExitStatus.PLAN_APPROVAL_REQUESTED,
                content=(
                    "The current Plan passed Hard Gate review and is waiting "
                    "for user approval."
                ),
            ),
        )
