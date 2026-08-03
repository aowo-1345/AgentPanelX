"""Project Runtime execution for the Bash tool."""

from agentplanex.domains import (
    ProjectRuntimeContext,
    ToolArguments,
    ToolExecutionResult,
)
from agentplanex.infrastructure import run_local_shell
from agentplanex.project_owner_agent.tools import BASH_TOOL
from agentplanex.project_runtime.executions.base import (
    ProjectExecution,
    project_execution,
)


@project_execution(BASH_TOOL)
class BashExecution(ProjectExecution):
    """Execute Bash commands within the bound project and runtime limits."""

    def execute(
        self,
        _context: ProjectRuntimeContext,
        arguments: ToolArguments,
    ) -> ToolExecutionResult:
        command = arguments.get("command")
        if not isinstance(command, str) or not command.strip():
            return ToolExecutionResult(
                output={
                    "output": "",
                    "returncode": -1,
                    "exception_info": "Bash action has no non-empty command",
                }
            )

        settings = self.dependencies.settings.bash
        return ToolExecutionResult(
            output=run_local_shell(
                command,
                cwd=self.dependencies.project_path,
                timeout_seconds=settings.timeout_seconds,
                output_limit=settings.output_limit,
            )
        )
