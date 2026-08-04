"""Project Runtime execution for requesting the next ordered Milestone Run."""

from agentplanex.domains import (
    AgentExit,
    AgentExitStatus,
    ProjectRuntimeContext,
    ToolArguments,
    ToolExecutionResult,
)
from agentplanex.project_owner_agent.tools import RUN_NEXT_MILESTONE_TOOL
from agentplanex.project_runtime.executions.base import (
    ProjectExecution,
    project_execution,
)
from agentplanex.services.delivery import (
    DeliveryError,
    FirstRunApprovalRequested,
)


@project_execution(RUN_NEXT_MILESTONE_TOOL)
class RunNextMilestoneExecution(ProjectExecution):
    """Queue only the first pending Milestone selected by Delivery Service."""

    def execute(
        self,
        context: ProjectRuntimeContext,
        arguments: ToolArguments,
    ) -> ToolExecutionResult:
        if arguments:
            return ToolExecutionResult(
                output={
                    "ok": False,
                    "error": "run_next_milestone does not accept arguments",
                }
            )
        try:
            result = self.dependencies.delivery.request_next_milestone(context)
        except DeliveryError as error:
            return ToolExecutionResult(output={"ok": False, "error": str(error)})

        if isinstance(result, FirstRunApprovalRequested):
            return ToolExecutionResult(
                output={
                    "ok": True,
                    "state": "FIRST_RUN_APPROVAL_REQUESTED",
                    "triage_id": result.context.triage_id,
                    "status": result.context.status,
                    "pending_action": result.context.pending_action,
                    "snapshot_id": result.snapshot.snapshot_id,
                    "milestone_key": result.milestone.key,
                },
                exit=AgentExit(
                    status=AgentExitStatus.FIRST_RUN_APPROVAL_REQUESTED,
                    content=(
                        "The first Milestone Run is ready and waiting for explicit user "
                        "Start approval."
                    ),
                ),
            )
        return ToolExecutionResult(
            output={
                "ok": True,
                "state": "MILESTONE_RUN_QUEUED",
                "triage_id": result.context.triage_id,
                "status": result.context.status,
                "run_id": result.stage_run.run_id,
                "stage_run_id": result.stage_run.stage_run_id,
                "snapshot_id": result.snapshot.snapshot_id,
                "milestone_key": result.milestone.key,
                "stage_key": result.stage.key,
                "input_commit_sha": result.stage_run.input_commit_sha,
            },
            exit=AgentExit(
                status=AgentExitStatus.MILESTONE_RUN_QUEUED,
                content=(
                    "The next Milestone Run has been queued for the Delivery Driver."
                ),
            ),
        )
