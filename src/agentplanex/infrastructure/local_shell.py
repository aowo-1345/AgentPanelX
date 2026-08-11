"""Local Bash execution for development and standalone entry points."""

import os
import shutil
import signal
import subprocess
from collections.abc import Mapping
from pathlib import Path

from agentplanex.domains import ActionOutput

_INHERITED_ENVIRONMENT = (
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "PATH",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "TERM",
    "TZ",
)


def run_local_shell(
    command: str,
    *,
    cwd: Path,
    timeout_seconds: float = 30.0,
    output_limit: int = 10_000,
    env: Mapping[str, str] | None = None,
) -> ActionOutput:
    """Execute one Bash command with writes confined to the requested project."""
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

    sandbox_environment = _sandbox_environment(env)
    bwrap = shutil.which("bwrap", path=sandbox_environment["PATH"])
    bash = shutil.which("bash", path=sandbox_environment["PATH"])
    if bwrap is None or bash is None:
        missing = "Bubblewrap" if bwrap is None else "Bash"
        return {
            "output": "",
            "returncode": -1,
            "exception_info": f"{missing} is required for Project Owner Bash",
        }

    project_root = cwd.resolve()
    if project_root == Path("/") or project_root == Path("/tmp"):
        return {
            "output": "",
            "returncode": -1,
            "exception_info": (
                f"Project Owner Bash requires a narrower project root than {project_root}"
            ),
        }
    if project_root.is_relative_to(Path("/run")):
        return {
            "output": "",
            "returncode": -1,
            "exception_info": "Project Owner Bash cannot use a host runtime directory",
        }
    try:
        completed = _run_bash(
            command,
            project_root=project_root,
            env=sandbox_environment,
            bwrap=bwrap,
            bash=bash,
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
    project_root: Path,
    env: dict[str, str],
    bwrap: str,
    bash: str,
    timeout_seconds: float,
) -> subprocess.CompletedProcess[str]:
    sandbox_command = _sandbox_command(
        command,
        project_root=project_root,
        env=env,
        bwrap=bwrap,
        bash=bash,
    )
    process = subprocess.Popen(
        sandbox_command,
        text=True,
        cwd=project_root,
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


def _sandbox_environment(overrides: Mapping[str, str] | None) -> dict[str, str]:
    environment = {
        name: value
        for name in _INHERITED_ENVIRONMENT
        if (value := os.environ.get(name)) is not None
    }
    environment.setdefault("PATH", os.defpath)
    environment.update(overrides or {})
    environment.update(
        {
            "GIT_OPTIONAL_LOCKS": "0",
            "HOME": "/tmp/home",
            "UV_CACHE_DIR": "/tmp/uv-cache",
            "XDG_CACHE_HOME": "/tmp/cache",
        }
    )
    return environment


def _sandbox_command(
    command: str,
    *,
    project_root: Path,
    env: Mapping[str, str],
    bwrap: str,
    bash: str,
) -> list[str]:
    root = str(project_root)
    arguments = [
        bwrap,
        "--die-with-parent",
        "--new-session",
        "--unshare-pid",
        "--unshare-net",
        "--ro-bind",
        "/",
        "/",
        "--tmpfs",
        "/tmp",
        "--tmpfs",
        "/run",
    ]
    if project_root.is_relative_to(Path("/tmp")):
        for directory in _directory_chain(Path("/tmp"), project_root):
            arguments.extend(("--dir", str(directory)))
    arguments.extend(
        (
            "--bind",
            root,
            root,
        )
    )
    for protected in (project_root / ".git", project_root / ".agentplanex"):
        path = str(protected)
        arguments.extend(("--ro-bind-try", path, path))
    arguments.extend(_resolver_bind_arguments())
    arguments.extend(
        (
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--clearenv",
        )
    )
    for name, value in sorted(env.items()):
        arguments.extend(("--setenv", name, value))
    arguments.extend(
        (
            "--dir",
            "/tmp/home",
            "--dir",
            "/tmp/cache",
            "--dir",
            "/tmp/uv-cache",
            "--chdir",
            root,
            "--",
            bash,
            "--noprofile",
            "--norc",
            "-c",
            command,
        )
    )
    return arguments


def _directory_chain(root: Path, target: Path) -> tuple[Path, ...]:
    relative = target.relative_to(root)
    return tuple(
        root.joinpath(*relative.parts[:index])
        for index in range(1, len(relative.parts) + 1)
    )


def _resolver_bind_arguments() -> tuple[str, ...]:
    resolver = Path("/etc/resolv.conf").resolve()
    runtime_root = Path("/run")
    if not resolver.is_file() or not resolver.is_relative_to(runtime_root):
        return ()

    arguments: list[str] = []
    for directory in _directory_chain(runtime_root, resolver.parent):
        arguments.extend(("--dir", str(directory)))
    arguments.extend(("--ro-bind", str(resolver), str(resolver)))
    return tuple(arguments)


def _truncate(output: str, limit: int) -> str:
    if len(output) <= limit:
        return output
    marker = f"\n... output truncated to {limit} characters ...\n"
    head = max(0, (limit - len(marker)) // 2)
    tail = max(0, limit - len(marker) - head)
    return output[:head] + marker + (output[-tail:] if tail else "")
