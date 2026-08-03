"""Project Runtime execution for the Bash tool."""

from pathlib import Path

from agentplanex.domains import ActionOutput, ToolArguments
from agentplanex.services import run_shell_command


def execute(
    arguments: ToolArguments,
    *,
    cwd: Path,
    timeout_seconds: float,
    output_limit: int,
) -> ActionOutput:
    """Validate a Bash action and invoke the shared shell service."""
    command = arguments.get("command")
    if not isinstance(command, str) or not command.strip():
        return {
            "output": "",
            "returncode": -1,
            "exception_info": "Bash action has no non-empty command",
        }
    return run_shell_command(
        command,
        cwd=cwd,
        timeout_seconds=timeout_seconds,
        output_limit=output_limit,
    )
