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
            updated = self.dependencies.planning.request_plan_approval(context)
        except PlanningError as error:
            return ToolExecutionResult(
                output={
                    "ok": False,
                    "error": str(error),
                }
            )

        return ToolExecutionResult(
            output={
                "ok": True,
                "triage_id": updated.triage_id,
                "status": updated.status,
                "pending_action": updated.pending_action,
            },
            exit=AgentExit(
                status=AgentExitStatus.PLAN_APPROVAL_REQUESTED,
                content="The current Plan is waiting for user approval.",
            ),
        )
