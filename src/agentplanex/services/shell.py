"""Shell application service."""

from pathlib import Path

from agentplanex.domains import ActionOutput
from agentplanex.infrastructure import run_local_shell


def run_shell_command(
    command: str,
    *,
    cwd: Path,
    timeout_seconds: float,
    output_limit: int,
) -> ActionOutput:
    """Run one command using the current Project Runtime binding."""
    return run_local_shell(
        command,
        cwd=cwd,
        timeout_seconds=timeout_seconds,
        output_limit=output_limit,
    )
