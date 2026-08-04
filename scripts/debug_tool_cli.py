"""Drive Tool Actions and user interactions against a project Runtime."""

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, TextIO
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from const import TARGET_PROJECT  # noqa: E402

from agentplanex.bootstrap import create_project_runtime  # noqa: E402
from agentplanex.domains import (  # noqa: E402
    Action,
    AgentExit,
    OwnerActivation,
    OwnerActivationStatus,
    ToolExecutionResult,
)
from agentplanex.services.owner_activation import ActivationDriveResult  # noqa: E402
from agentplanex.services.planning import PlanDecision  # noqa: E402

type ToolRunner = Callable[[Action], ToolExecutionResult]
type InputReader = Callable[[str], str]
type InteractionAction = Literal["message", "approve", "reject", "drive"]


class RuntimeCommands(Protocol):
    def submit_message(self, content: str) -> OwnerActivation: ...

    def approve_plan(self) -> PlanDecision: ...

    def reject_plan(self, feedback: str = "") -> PlanDecision: ...

    def drive_next_activation(self) -> ActivationDriveResult: ...

    def execute_action(self, action: Action) -> ToolExecutionResult: ...


@dataclass(frozen=True, slots=True)
class _Interaction:
    action: InteractionAction
    message: str = ""


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    action_text = " ".join(args.action).strip()
    if args.print_mode and not action_text:
        _print_error("Command must not be empty")
        return 2

    command: Action | _Interaction | None = None
    if args.print_mode:
        try:
            command = _parse_command(action_text)
        except ValueError as error:
            _print_error(str(error))
            return 2

    try:
        runtime = create_project_runtime(
            project_path=args.cwd,
            approval_mode="yolo",
        )
    except ValueError as error:
        _print_error(str(error))
        return 2

    if command is not None:
        return _dispatch(runtime, command)
    return _run_interactive(runtime, action_text)


def _execute_once(
    execute_tool: ToolRunner,
    action: Action,
    *,
    stdout: TextIO | None = None,
) -> int:
    output = stdout if stdout is not None else sys.stdout
    try:
        result = execute_tool(action)
    except Exception as error:
        _print_result(
            action,
            result=None,
            error=f"{type(error).__name__}: {error}",
            output=output,
        )
        return 1

    _print_result(action, result=result, error=None, output=output)
    return 0 if _succeeded(result) else 1


def _run_interactive(
    runtime: RuntimeCommands,
    initial_action: str = "",
    *,
    read_input: InputReader = input,
    stdout: TextIO | None = None,
) -> int:
    action_text = initial_action
    while True:
        if not action_text:
            try:
                action_text = read_input("> ").strip()
            except EOFError:
                return 0
        if not action_text:
            continue
        if action_text in {"/exit", "/quit"}:
            return 0

        try:
            command = _parse_command(action_text)
        except ValueError as error:
            _print_error(str(error), output=stdout)
        else:
            _dispatch(runtime, command, stdout=stdout)
        action_text = ""


def _dispatch(
    runtime: RuntimeCommands,
    command: Action | _Interaction,
    *,
    stdout: TextIO | None = None,
) -> int:
    if not isinstance(command, _Interaction):
        return _execute_once(runtime.execute_action, command, stdout=stdout)
    if command.action == "message":
        return _submit_message(runtime, command.message, stdout=stdout)
    if command.action == "approve":
        return _submit_plan_decision(runtime, "approve", "", stdout=stdout)
    if command.action == "reject":
        return _submit_plan_decision(
            runtime,
            "reject",
            command.message,
            stdout=stdout,
        )
    return _drive_once(runtime, stdout=stdout)


def _parse_command(command_text: str) -> Action | _Interaction:
    stripped = command_text.strip()
    if stripped.startswith("{"):
        return _parse_action(stripped)
    if stripped == "tool" or stripped.startswith("tool "):
        return _parse_action(stripped.removeprefix("tool").strip())

    command, separator, message = stripped.partition(" ")
    if command == "approve":
        if separator:
            raise ValueError("approve does not accept a message")
        return _Interaction("approve")
    if command == "reject":
        return _Interaction("reject", message.strip())
    if command == "message":
        if not message.strip():
            raise ValueError("message content must not be empty")
        return _Interaction("message", message.strip())
    if command == "drive":
        if separator:
            raise ValueError("drive does not accept a message")
        return _Interaction("drive")
    return _Interaction("message", stripped)


def _parse_action(action_text: str) -> Action:
    try:
        parsed: object = json.loads(action_text)
    except json.JSONDecodeError as error:
        raise ValueError(
            f"Tool action must be a JSON object: {error.msg}"
        ) from error
    if not isinstance(parsed, dict):
        raise ValueError("Tool action must be a JSON object")

    tool = parsed.get("tool")
    if not isinstance(tool, str) or not tool.strip():
        raise ValueError("Tool action must contain a non-empty string 'tool'")
    arguments = parsed.get("arguments")
    if not isinstance(arguments, dict):
        raise ValueError("Tool action must contain an object 'arguments'")

    call_id = parsed.get("call_id")
    if call_id is None:
        parsed["call_id"] = uuid4().hex
    elif not isinstance(call_id, str) or not call_id.strip():
        raise ValueError("Tool action 'call_id' must be a non-empty string")
    return parsed


def _submit_message(
    runtime: RuntimeCommands,
    message: str,
    *,
    stdout: TextIO | None = None,
) -> int:
    output = stdout if stdout is not None else sys.stdout
    try:
        activation = runtime.submit_message(message)
    except Exception as error:
        _print_command_error("message", error, output)
        return 1
    print(
        json.dumps(
            {
                "action": "message",
                "ok": True,
                "activation": _activation_json(activation),
            },
            ensure_ascii=False,
        ),
        file=output,
    )
    return 0


def _submit_plan_decision(
    runtime: RuntimeCommands,
    action: Literal["approve", "reject"],
    feedback: str,
    *,
    stdout: TextIO | None = None,
) -> int:
    output = stdout if stdout is not None else sys.stdout
    try:
        decision = (
            runtime.approve_plan()
            if action == "approve"
            else runtime.reject_plan(feedback)
        )
    except Exception as error:
        _print_command_error(action, error, output)
        return 1
    print(
        json.dumps(
            {
                "action": action,
                "ok": True,
                "result": {
                    "status": decision.context.status,
                    "pending_action": decision.context.pending_action,
                    "plan_commit_sha": decision.commit_sha,
                },
                "activation": _activation_json(decision.activation),
            },
            ensure_ascii=False,
        ),
        file=output,
    )
    return 0


def _drive_once(
    runtime: RuntimeCommands,
    *,
    stdout: TextIO | None = None,
) -> int:
    output = stdout if stdout is not None else sys.stdout
    try:
        driven = runtime.drive_next_activation()
    except Exception as error:
        _print_command_error("drive", error, output)
        return 1
    succeeded = (
        driven.activation is None
        or driven.activation.status is OwnerActivationStatus.COMPLETED
    )
    print(
        json.dumps(
            {
                "action": "drive",
                "ok": succeeded,
                "claimed": driven.activation is not None,
                "activation": (
                    _activation_json(driven.activation)
                    if driven.activation is not None
                    else None
                ),
                "result": _agent_exit_json(driven.exit),
            },
            ensure_ascii=False,
        ),
        file=output,
    )
    return 0 if succeeded else 1


def _activation_json(activation: OwnerActivation) -> dict[str, object]:
    return {
        "activation_id": activation.activation_id,
        "task_type": activation.task_type.value,
        "message_id": activation.message_id,
        "status": activation.status.value,
        "created_at": activation.created_at.isoformat(),
        "started_at": (
            activation.started_at.isoformat()
            if activation.started_at is not None
            else None
        ),
        "finished_at": (
            activation.finished_at.isoformat()
            if activation.finished_at is not None
            else None
        ),
        "failure": activation.failure,
    }


def _agent_exit_json(result: AgentExit | None) -> dict[str, str] | None:
    return (
        {"status": result.status.value, "content": result.content}
        if result is not None
        else None
    )


def _print_command_error(action: str, error: Exception, output: TextIO) -> None:
    print(
        json.dumps(
            {
                "action": action,
                "ok": False,
                "error": f"{type(error).__name__}: {error}",
            },
            ensure_ascii=False,
        ),
        file=output,
    )


def _succeeded(result: ToolExecutionResult) -> bool:
    explicit = result.output.get("ok")
    if isinstance(explicit, bool):
        return explicit
    returncode = result.output.get("returncode")
    exception_info = result.output.get("exception_info")
    return returncode in {None, 0} and not exception_info


def _print_error(message: str, *, output: TextIO | None = None) -> None:
    destination = output if output is not None else sys.stdout
    print(
        json.dumps(
            {
                "call_id": None,
                "tool": None,
                "ok": False,
                "result": None,
                "exit": None,
                "error": message,
            },
            ensure_ascii=False,
        ),
        file=destination,
    )


def _print_result(
    action: Action,
    *,
    result: ToolExecutionResult | None,
    error: str | None,
    output: TextIO,
) -> None:
    exit_result = result.exit if result is not None else None
    response = {
        "call_id": action.get("call_id"),
        "tool": action.get("tool"),
        "ok": result is not None and _succeeded(result),
        "result": result.output if result is not None else None,
        "exit": (
            {
                "status": exit_result.status.value,
                "content": exit_result.content,
            }
            if exit_result is not None
            else None
        ),
    }
    if error is not None:
        response["error"] = error
    print(json.dumps(response, ensure_ascii=False), file=output)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Debug Tool Actions and user interactions against a project Runtime"
    )
    parser.add_argument("action", nargs="*")
    parser.add_argument(
        "-p",
        "--print",
        dest="print_mode",
        action="store_true",
        help="execute one command and exit",
    )
    parser.add_argument("--cwd", type=Path, default=TARGET_PROJECT)
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
