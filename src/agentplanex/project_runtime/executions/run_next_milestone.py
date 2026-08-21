"""Project Runtime execution for requesting the next ordered Milestone Run."""

from agentplanex.domains import (
    AgentExit,
    AgentExitStatus,
    ProjectRuntimeState,
    ToolExecutionResult,
)
from agentplanex.project_owner_agent.tools import NoToolArguments, ToolDefinition
from agentplanex.project_runtime.executions.base import (
    ProjectExecution,
    project_execution,
)
from agentplanex.services.delivery import (
    DeliveryError,
    FirstRunApprovalRequested,
)

RUN_NEXT_MILESTONE_TOOL_NAME = "run_next_milestone"
RUN_NEXT_MILESTONE_DESCRIPTION = (
    "Request the first unfinished Milestone from the current complete View. The first "
    "call requests explicit user Start approval; later calls queue delivery. After a "
    "terminal Stage failure, a BLOCKED project may retry the same first unfinished "
    "Milestone when the approved Plan and Snapshot remain valid."
)
RUN_NEXT_MILESTONE_TOOL = ToolDefinition(
    name=RUN_NEXT_MILESTONE_TOOL_NAME,
    description=RUN_NEXT_MILESTONE_DESCRIPTION,
    arguments_type=NoToolArguments,
)


@project_execution(RUN_NEXT_MILESTONE_TOOL)
class RunNextMilestoneExecution(ProjectExecution[NoToolArguments]):
    """Queue only the first pending Milestone selected by Delivery Service."""

    def execute(
        self,
        _context: ProjectRuntimeState,
        arguments: NoToolArguments,
    ) -> ToolExecutionResult:
        try:
            result = self.dependencies.delivery.request_next_milestone()
        except DeliveryError as error:
            return ToolExecutionResult(output={"ok": False, "error": str(error)})

        if isinstance(result, FirstRunApprovalRequested):
            return ToolExecutionResult(
                output={
                    "ok": True,
                    "state": "FIRST_RUN_APPROVAL_REQUESTED",
                    "triage_id": result.state.triage_id,
                    "status": result.state.status,
                    "pending_action": result.state.pending_action,
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
                "triage_id": result.state.triage_id,
                "status": result.state.status,
                "run_id": result.run_id,
                "stage_run_id": result.stage_run_id,
                "snapshot_id": result.snapshot_id,
                "milestone_key": result.milestone_key,
                "stage_key": result.stage_key,
                "input_commit_sha": result.input_commit_sha,
            },
            exit=AgentExit(
                status=AgentExitStatus.MILESTONE_RUN_QUEUED,
                content=(
                    "The next Milestone Run has been queued for the Delivery Driver."
                ),
            ),
        )
