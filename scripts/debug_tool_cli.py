"""Drive Tool Actions and user interactions against a project Runtime."""

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from const import TARGET_PROJECT  # noqa: E402

from agentplanex.bootstrap import create_project_runtime  # noqa: E402
from agentplanex.domains import (  # noqa: E402
    Action,
    AgentExit,
    AgentExitStatus,
    ToolExecutionResult,
    UserInteractionAction,
)

type ToolRunner = Callable[[Action], ToolExecutionResult]
type InteractionRunner = Callable[[UserInteractionAction, str], AgentExit]
type InputReader = Callable[[str], str]


@dataclass(frozen=True, slots=True)
class _Interaction:
    action: UserInteractionAction
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

    if isinstance(command, _Interaction):
        return _interact_once(runtime.interact, command)
    if command is not None:
        return _execute_once(runtime.execute_action, command)
    return _run_interactive(
        runtime.execute_action,
        runtime.interact,
        action_text,
    )


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
    execute_tool: ToolRunner,
    interact: InteractionRunner,
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
            if isinstance(command, _Interaction):
                _interact_once(interact, command, stdout=stdout)
            else:
                _execute_once(execute_tool, command, stdout=stdout)
        action_text = ""


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


def _interact_once(
    interact: InteractionRunner,
    interaction: _Interaction,
    *,
    stdout: TextIO | None = None,
) -> int:
    output = stdout if stdout is not None else sys.stdout
    result = interact(interaction.action, interaction.message)
    succeeded = result.status is not AgentExitStatus.UNHANDLED_EXCEPTION
    print(
        json.dumps(
            {
                "action": interaction.action,
                "ok": succeeded,
                "result": {
                    "status": result.status.value,
                    "content": result.content,
                },
            },
            ensure_ascii=False,
        ),
        file=output,
    )
    return 0 if succeeded else 1


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
