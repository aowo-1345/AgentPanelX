"""Project-bound tool execution composition."""

from pathlib import Path

from agentplanex.domains import ActionOutput, ProjectRuntimeContext, ToolArguments
from agentplanex.project_owner_agent.tools import BASH_TOOL
from agentplanex.project_runtime.executions.base import (
    ProjectExecution,
    ProjectExecutions,
)
from agentplanex.project_runtime.executions.bash import execute as execute_bash
from agentplanex.settings import RuntimeSettings


def create_project_executions(
    project_path: Path,
    settings: RuntimeSettings,
) -> ProjectExecutions:
    """Bind supported tool executions to one project and its limits."""
    bash_settings = settings.bash

    def run_bash(
        _context: ProjectRuntimeContext,
        arguments: ToolArguments,
    ) -> ActionOutput:
        return execute_bash(
            arguments,
            cwd=project_path,
            timeout_seconds=bash_settings.timeout_seconds,
            output_limit=bash_settings.output_limit,
        )

    return ProjectExecutions(
        [
            ProjectExecution(
                definition=BASH_TOOL,
                handler=run_bash,
            )
        ]
    )


__all__ = ["ProjectExecutions", "create_project_executions"]
