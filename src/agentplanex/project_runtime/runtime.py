"""Project-scoped Runtime entry point and tool environment."""

from pathlib import Path

from agentplanex.domains import (
    Action,
    AgentExit,
    ProjectOwnerTask,
    ProjectOwnerTaskType,
    ToolExecutionResult,
    UserInteractionAction,
)
from agentplanex.infrastructure.sqlite import SQLiteDatabase, initialize_schema
from agentplanex.infrastructure.sqlite.timeline import SQLiteTimelineRecorder
from agentplanex.project_owner_agent.approval import ApprovalMode
from agentplanex.project_runtime.executions import create_project_executions
from agentplanex.services import (
    EventBus,
    PlanningService,
    ProjectRuntimeService,
    RuntimeContextService,
)
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

        database = SQLiteDatabase.for_project(project_path)
        initialize_schema(database)
        event_bus = EventBus((SQLiteTimelineRecorder(database),))
        runtime_contexts = RuntimeContextService(database, event_bus)
        planning = PlanningService(
            project_path=project_path,
            database=database,
            event_bus=event_bus,
            runtime_contexts=runtime_contexts,
        )
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
            event_bus=event_bus,
            runtime_contexts=runtime_contexts,
        )

    def run(self, task: str = "") -> AgentExit:
        """Run one Project Owner turn."""
        return self._service.run(
            ProjectOwnerTask(
                type=ProjectOwnerTaskType.USER_INPUT,
                content=task,
            )
        )

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
