"""Project-scoped Runtime entry point and tool environment."""

from pathlib import Path

from agentplanex.domains import (
    Action,
    AgentExit,
    ToolExecutionResult,
    UserInteractionAction,
)
from agentplanex.project_owner_agent.approval import ApprovalMode
from agentplanex.project_runtime.executions import create_project_executions
from agentplanex.services import PlanningService, ProjectRuntimeService
from agentplanex.settings import Settings


class ProjectRuntime:
    """Expose one long-lived Project Owner over project-scoped tools."""

    def __init__(
        self,
        *,
        project_path: Path,
        settings: Settings,
        approval_mode: ApprovalMode,
    ) -> None:
        project_path = project_path.resolve()
        if not project_path.is_dir():
            raise ValueError(f"Project path is not a directory: {project_path}")

        planning = PlanningService.for_project(project_path)
        executions = create_project_executions(
            project_path,
            settings.runtime,
            planning,
        )
        self._service = ProjectRuntimeService(
            project_path=project_path,
            settings=settings,
            approval_mode=approval_mode,
            tools=executions.tools,
            execute_tool=executions.execute,
            planning=planning,
        )

    def run(self, task: str = "") -> AgentExit:
        """Run one Project Owner turn."""
        return self._service.run(task)

    def execute_action(self, action: Action) -> ToolExecutionResult:
        """Execute one explicit tool action without entering the Agent loop."""
        return self._service.execute_action(action)

    def interact(
        self,
        action: UserInteractionAction = "message",
        message: str = "",
    ) -> AgentExit:
        """Apply one external user interaction and resume the Owner."""
        return self._service.interact(action=action, message=message)
