"""Project-bound tool execution composition."""

from pathlib import Path

from agentplanex.project_runtime.executions import bash as _bash  # noqa: F401
from agentplanex.project_runtime.executions import (  # noqa: F401
    request_plan_approval as _request_plan_approval,
)
from agentplanex.project_runtime.executions.base import (
    ProjectExecutionDependencies,
    ProjectExecutions,
)
from agentplanex.services.planning import PlanningService
from agentplanex.settings import RuntimeSettings


def create_project_executions(
    project_path: Path,
    settings: RuntimeSettings,
    planning: PlanningService | None = None,
) -> ProjectExecutions:
    """Create all registered executions for one project."""
    planning = planning or PlanningService.for_project(project_path)
    return ProjectExecutions(
        ProjectExecutionDependencies(
            project_path=project_path,
            settings=settings,
            planning=planning,
        )
    )


__all__ = ["ProjectExecutions", "create_project_executions"]
