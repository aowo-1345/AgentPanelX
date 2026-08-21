"""CLI composition root for the standalone Project Owner Agent."""

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol, TextIO

from agentplanex.bootstrap import create_project_runtime_control
from agentplanex.domains import AgentExitStatus, OwnerActivation, ProjectRuntimeState
from agentplanex.project_owner_agent.approval import ApprovalMode
from agentplanex.project_owner_agent.exception import ModelError
from agentplanex.services.project_runtime_context import ActivationDriveResult

type InputReader = Callable[[str], str]


class OwnerControl(Protocol):
    def initialize(self) -> ProjectRuntimeState: ...

    def submit_message(self, content: str) -> OwnerActivation: ...

    def drive_owner_model(self) -> ActivationDriveResult: ...


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    task = " ".join(args.task).strip()
    if args.print_mode and not task:
        print("Task must not be empty", file=sys.stderr)
        return 2

    approval_mode: ApprovalMode = args.mode
    try:
        runtime = create_project_runtime_control(
            project_path=args.cwd,
            approval_mode=approval_mode,
        )
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2

    runtime.initialize()

    if args.print_mode:
        return _run_once(runtime, task)
    return _run_interactive(runtime, task)


def _run_once(
    runtime: OwnerControl,
    task: str,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    output = stdout if stdout is not None else sys.stdout
    error_output = stderr if stderr is not None else sys.stderr
    try:
        runtime.submit_message(task)
        driven = runtime.drive_owner_model()
    except (ModelError, ValueError) as error:
        print(str(error), file=error_output)
        return 1

    result = driven.exit
    if result is None:
        print("No Project Owner activation was claimed", file=error_output)
        return 1
    if result.status is AgentExitStatus.REPLY_TO_HUMAN:
        print(result.content, file=output)
        return 0
    print(result.content, file=error_output)
    return 1


def _run_interactive(
    runtime: OwnerControl,
    initial_task: str = "",
    *,
    read_input: InputReader = input,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    task = initial_task
    while True:
        if not task:
            try:
                task = read_input("> ").strip()
            except EOFError:
                return 0
        if not task:
            continue
        if task in {"/exit", "/quit"}:
            return 0

        _run_once(
            runtime,
            task,
            stdout=stdout,
            stderr=stderr,
        )
        task = ""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Project Owner Agent")
    parser.add_argument("task", nargs="*")
    parser.add_argument(
        "-p",
        "--print",
        dest="print_mode",
        action="store_true",
        help="run one task and exit",
    )
    parser.add_argument(
        "--mode",
        choices=("confirm", "yolo"),
        default="confirm",
    )
    parser.add_argument("--cwd", type=Path, default=Path.cwd())
    return parser
