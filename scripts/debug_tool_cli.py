"""Execute explicit Tool Actions against the local project Runtime."""

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TextIO
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from const import TARGET_PROJECT  # noqa: E402

from agentplanex.bootstrap import create_project_runtime  # noqa: E402
from agentplanex.domains import Action, ToolExecutionResult  # noqa: E402

type ToolRunner = Callable[[Action], ToolExecutionResult]
type InputReader = Callable[[str], str]


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    action_text = " ".join(args.action).strip()
    if args.print_mode and not action_text:
        _print_error("Tool action must not be empty")
        return 2

    action: Action | None = None
    if args.print_mode:
        try:
            action = _parse_action(action_text)
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

    if action is not None:
        return _execute_once(runtime.execute_action, action)
    return _run_interactive(runtime.execute_action, action_text)


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
            action = _parse_action(action_text)
        except ValueError as error:
            _print_error(str(error), output=stdout)
        else:
            _execute_once(execute_tool, action, stdout=stdout)
        action_text = ""


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
        description="Execute Tool Actions against a project Runtime"
    )
    parser.add_argument("action", nargs="*")
    parser.add_argument(
        "-p",
        "--print",
        dest="print_mode",
        action="store_true",
        help="execute one Tool Action and exit",
    )
    parser.add_argument("--cwd", type=Path, default=TARGET_PROJECT)
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
