"""Project-bound tool execution composition."""

from pathlib import Path

from agentplanex.project_runtime.executions import bash as _bash  # noqa: F401
from agentplanex.project_runtime.executions.base import (
    ProjectExecutionDependencies,
    ProjectExecutions,
)
from agentplanex.settings import RuntimeSettings


def create_project_executions(
    project_path: Path,
    settings: RuntimeSettings,
) -> ProjectExecutions:
    """Create all registered executions for one project."""
    return ProjectExecutions(
        ProjectExecutionDependencies(
            project_path=project_path,
            settings=settings,
        )
    )


__all__ = ["ProjectExecutions", "create_project_executions"]
