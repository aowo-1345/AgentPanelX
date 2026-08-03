"""Local Bash execution for development and standalone entry points."""

import os
import signal
import subprocess
from collections.abc import Mapping
from pathlib import Path

from agentplanex.domains import ActionOutput


def run_local_shell(
    command: str,
    *,
    cwd: Path,
    timeout_seconds: float = 30.0,
    output_limit: int = 65_536,
    env: Mapping[str, str] | None = None,
) -> ActionOutput:
    """Execute one Bash command in the requested working directory."""
    if not cwd.is_dir():
        raise ValueError(f"Bash cwd is not a directory: {cwd}")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    if output_limit <= 0:
        raise ValueError("output_limit must be positive")
    if not command.strip():
        return {
            "output": "",
            "returncode": -1,
            "exception_info": "Bash action has no non-empty command",
        }

    try:
        completed = _run_bash(
            command,
            cwd=cwd,
            env=os.environ | dict(env or {}),
            timeout_seconds=timeout_seconds,
        )
        return {
            "output": _truncate(completed.stdout, output_limit),
            "returncode": completed.returncode,
            "exception_info": "",
        }
    except subprocess.TimeoutExpired as error:
        output = error.output if isinstance(error.output, str) else ""
        return {
            "output": _truncate(output, output_limit),
            "returncode": -1,
            "exception_info": f"Bash command timed out after {timeout_seconds:g}s",
        }
    except OSError as error:
        return {
            "output": "",
            "returncode": -1,
            "exception_info": f"Failed to start Bash: {error}",
        }


def _run_bash(
    command: str,
    *,
    cwd: Path,
    env: dict[str, str],
    timeout_seconds: float,
) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        ["bash", "-lc", command],
        text=True,
        cwd=cwd,
        env=env,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=os.name == "posix",
    )
    try:
        stdout, _ = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
        stdout, _ = process.communicate()
        raise subprocess.TimeoutExpired(command, timeout_seconds, output=stdout) from None
    return subprocess.CompletedProcess(command, process.returncode, stdout=stdout)


def _truncate(output: str, limit: int) -> str:
    if len(output) <= limit:
        return output
    marker = f"\n... output truncated to {limit} characters ...\n"
    head = max(0, (limit - len(marker)) // 2)
    tail = max(0, limit - len(marker) - head)
    return output[:head] + marker + (output[-tail:] if tail else "")
