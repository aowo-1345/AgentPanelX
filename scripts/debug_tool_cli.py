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
    AgentExitStatus,
    OwnerActivation,
    OwnerActivationStatus,
    StageRun,
    ToolExecutionResult,
)
from agentplanex.services.delivery import MilestoneRunQueued  # noqa: E402
from agentplanex.services.delivery_runner import DeliveryDriveResult  # noqa: E402
from agentplanex.services.owner_activation import ActivationDriveResult  # noqa: E402
from agentplanex.services.planning import PlanDecision  # noqa: E402
from agentplanex.services.project_control import ProjectControlView  # noqa: E402
from agentplanex.services.project_runtime import (  # noqa: E402
    ToolActivationDriveResult,
)

type ToolRunner = Callable[[Action], ToolExecutionResult]
type InputReader = Callable[[str], str]
type InteractionAction = Literal[
    "message",
    "approve",
    "reject",
    "drive",
    "drive-tool",
    "drive-reply",
    "drive-fail",
    "start",
    "drive-delivery",
    "view",
]


class RuntimeCommands(Protocol):
    def submit_message(self, content: str) -> OwnerActivation: ...

    def approve_plan(self) -> PlanDecision: ...

    def reject_plan(self, feedback: str = "") -> PlanDecision: ...

    def drive_next_activation(self) -> ActivationDriveResult: ...

    def drive_activation_tool(self, action: Action) -> ToolActivationDriveResult: ...

    def reply_to_activation(self, content: str) -> ToolActivationDriveResult: ...

    def fail_activation(self, reason: str) -> ToolActivationDriveResult: ...

    def start_first_run(self) -> MilestoneRunQueued: ...

    def drive_delivery(self) -> DeliveryDriveResult: ...

    def project_control_view(self) -> ProjectControlView: ...

    def execute_action(self, action: Action) -> ToolExecutionResult: ...


@dataclass(frozen=True, slots=True)
class _Interaction:
    action: InteractionAction
    message: str = ""
    tool_action: Action | None = None


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
    if command.action == "drive":
        return _drive_once(runtime, stdout=stdout)
    if command.action == "drive-tool":
        if command.tool_action is None:
            raise RuntimeError("Tool drive is missing its Tool Action")
        return _drive_tool_once(runtime, command.tool_action, stdout=stdout)
    if command.action == "drive-reply":
        return _drive_reply_once(runtime, command.message, stdout=stdout)
    if command.action == "drive-fail":
        return _drive_fail_once(runtime, command.message, stdout=stdout)
    if command.action == "start":
        return _start_first_run(runtime, stdout=stdout)
    if command.action == "drive-delivery":
        return _drive_delivery_once(runtime, stdout=stdout)
    if command.action == "view":
        return _show_view(runtime, stdout=stdout)
    raise AssertionError(f"Unhandled interaction action: {command.action}")


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
        if not separator:
            return _Interaction("drive")
        driver, driver_separator, payload = message.strip().partition(" ")
        if driver == "model":
            if driver_separator:
                raise ValueError("drive model does not accept a message")
            return _Interaction("drive")
        if driver == "tool":
            if not driver_separator or not payload.strip():
                raise ValueError("drive tool requires a Tool Action JSON object")
            return _Interaction(
                "drive-tool",
                tool_action=_parse_action(payload.strip()),
            )
        if driver == "reply":
            if not driver_separator or not payload.strip():
                raise ValueError("drive reply content must not be empty")
            return _Interaction("drive-reply", payload.strip())
        if driver == "fail":
            if not driver_separator or not payload.strip():
                raise ValueError("drive fail reason must not be empty")
            return _Interaction("drive-fail", payload.strip())
        raise ValueError("drive mode must be model, tool, reply, or fail")
    if command == "start":
        if separator:
            raise ValueError("start does not accept a message")
        return _Interaction("start")
    if command == "drive-delivery":
        if separator:
            raise ValueError("drive-delivery does not accept a message")
        return _Interaction("drive-delivery")
    if command == "view":
        if separator:
            raise ValueError("view does not accept a message")
        return _Interaction("view")
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
                "driver_mode": "MODEL",
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


def _drive_tool_once(
    runtime: RuntimeCommands,
    action: Action,
    *,
    stdout: TextIO | None = None,
) -> int:
    output = stdout if stdout is not None else sys.stdout
    try:
        driven = runtime.drive_activation_tool(action)
    except Exception as error:
        _print_command_error("drive tool", error, output)
        return 1

    tool_result = driven.tool_result
    succeeded = (
        tool_result is not None
        and _succeeded(tool_result)
        and driven.activation.status is not OwnerActivationStatus.FAILED
    )
    print(
        json.dumps(
            {
                "action": "drive",
                "driver_mode": "TOOL",
                "step": "tool",
                "ok": succeeded,
                "started": driven.started,
                "activation": _activation_json(driven.activation),
                "tool_action": {
                    "call_id": action.get("call_id"),
                    "tool": action.get("tool"),
                },
                "result": (
                    tool_result.output if tool_result is not None else None
                ),
                "exit": _agent_exit_json(driven.exit),
            },
            ensure_ascii=False,
        ),
        file=output,
    )
    return 0 if succeeded else 1


def _drive_reply_once(
    runtime: RuntimeCommands,
    content: str,
    *,
    stdout: TextIO | None = None,
) -> int:
    return _finish_tool_drive(
        "reply",
        lambda: runtime.reply_to_activation(content),
        stdout=stdout,
    )


def _drive_fail_once(
    runtime: RuntimeCommands,
    reason: str,
    *,
    stdout: TextIO | None = None,
) -> int:
    return _finish_tool_drive(
        "fail",
        lambda: runtime.fail_activation(reason),
        stdout=stdout,
    )


def _finish_tool_drive(
    step: Literal["reply", "fail"],
    finish: Callable[[], ToolActivationDriveResult],
    *,
    stdout: TextIO | None = None,
) -> int:
    output = stdout if stdout is not None else sys.stdout
    try:
        driven = finish()
    except Exception as error:
        _print_command_error(f"drive {step}", error, output)
        return 1

    expected_status = (
        AgentExitStatus.REPLY_TO_HUMAN
        if step == "reply"
        else AgentExitStatus.MANUAL_DRIVE_FAILED
    )
    succeeded = driven.exit is not None and driven.exit.status is expected_status
    print(
        json.dumps(
            {
                "action": "drive",
                "driver_mode": "TOOL",
                "step": step,
                "ok": succeeded,
                "started": driven.started,
                "activation": _activation_json(driven.activation),
                "result": _agent_exit_json(driven.exit),
            },
            ensure_ascii=False,
        ),
        file=output,
    )
    return 0 if succeeded else 1


def _start_first_run(
    runtime: RuntimeCommands,
    *,
    stdout: TextIO | None = None,
) -> int:
    output = stdout if stdout is not None else sys.stdout
    try:
        queued = runtime.start_first_run()
    except Exception as error:
        _print_command_error("start", error, output)
        return 1
    print(
        json.dumps(
            {
                "action": "start",
                "ok": True,
                "result": {
                    "status": queued.context.status,
                    "run_id": queued.stage_run.run_id,
                    "stage_run_id": queued.stage_run.stage_run_id,
                    "snapshot_id": queued.snapshot.snapshot_id,
                    "milestone_key": queued.milestone.key,
                    "stage_key": queued.stage.key,
                    "input_commit_sha": queued.stage_run.input_commit_sha,
                },
            },
            ensure_ascii=False,
        ),
        file=output,
    )
    return 0


def _drive_delivery_once(
    runtime: RuntimeCommands,
    *,
    stdout: TextIO | None = None,
) -> int:
    output = stdout if stdout is not None else sys.stdout
    try:
        driven = runtime.drive_delivery()
    except Exception as error:
        _print_command_error("drive-delivery", error, output)
        return 1
    print(
        json.dumps(
            {
                "action": "drive-delivery",
                "ok": True,
                "result": {
                    "outcome": driven.outcome,
                    "context_status": driven.context_status,
                    "candidate_commit_sha": driven.candidate_commit_sha,
                    "stage_run": (
                        _stage_run_json(driven.stage_run)
                        if driven.stage_run is not None
                        else None
                    ),
                },
                "activation": (
                    _activation_json(driven.activation)
                    if driven.activation is not None
                    else None
                ),
            },
            ensure_ascii=False,
        ),
        file=output,
    )
    return 0


def _show_view(
    runtime: RuntimeCommands,
    *,
    stdout: TextIO | None = None,
) -> int:
    output = stdout if stdout is not None else sys.stdout
    try:
        view = runtime.project_control_view()
    except Exception as error:
        _print_command_error("view", error, output)
        return 1
    print(
        json.dumps(
            {
                "action": "view",
                "ok": True,
                "view": _project_control_view_json(view),
            },
            ensure_ascii=False,
        ),
        file=output,
    )
    return 0


def _activation_json(activation: OwnerActivation) -> dict[str, object]:
    return {
        "activation_id": activation.activation_id,
        "task_type": activation.task_type.value,
        "message_id": activation.message_id,
        "summary_id": activation.summary_id,
        "status": activation.status.value,
        "driver_mode": (
            activation.driver_mode.value
            if activation.driver_mode is not None
            else None
        ),
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


def _project_control_view_json(view: ProjectControlView) -> dict[str, object]:
    context = view.context
    snapshot = view.snapshot
    return {
        "context": {
            "triage_id": context.triage_id,
            "status": context.status,
            "pending_action": context.pending_action,
            "git_branch": context.git_branch,
            "git_main_version": context.git_main_version,
            "rolling_started_at": (
                context.rolling_started_at.isoformat()
                if context.rolling_started_at is not None
                else None
            ),
            "current_plan_commit_sha": context.current_plan_commit_sha,
            "current_snapshot_id": context.current_snapshot_id,
            "current_run_id": context.current_run_id,
            "current_milestone_key": context.current_milestone_key,
            "current_stage_key": context.current_stage_key,
            "current_candidate_commit_sha": context.current_candidate_commit_sha,
        },
        "snapshot": (
            {
                "snapshot_id": snapshot.snapshot_id,
                "previous_snapshot_id": snapshot.previous_snapshot_id,
                "plan_commit_sha": snapshot.plan_commit_sha,
                "reason": snapshot.reason,
                "created_at": snapshot.created_at.isoformat(),
                "milestones": [
                    {
                        "key": milestone.key,
                        "objective": milestone.objective,
                        "state": milestone.state.value,
                        "stages": [
                            {"key": stage.key, "objective": stage.objective}
                            for stage in milestone.stages
                        ],
                    }
                    for milestone in snapshot.milestones
                ],
            }
            if snapshot is not None
            else None
        ),
        "stage_runs": [_stage_run_json(stage_run) for stage_run in view.stage_runs],
        "owner_activation": (
            _activation_json(view.owner_activation)
            if view.owner_activation is not None
            else None
        ),
        "timeline": [
            {
                "event_id": event.event_id,
                "event_type": event.event_type.value,
                "react_loop_id": event.react_loop_id,
                "message_id": event.message_id,
                "payload": event.payload,
                "created_at": event.created_at.isoformat(),
            }
            for event in view.timeline
        ],
        "git": {"branch": view.git_branch, "head": view.git_head},
        "allowed_actions": list(view.allowed_actions),
    }


def _stage_run_json(stage_run: StageRun) -> dict[str, object]:
    return {
        "stage_run_id": stage_run.stage_run_id,
        "run_id": stage_run.run_id,
        "snapshot_id": stage_run.snapshot_id,
        "milestone_key": stage_run.milestone_key,
        "stage_key": stage_run.stage_key,
        "status": stage_run.status.value,
        "input_commit_sha": stage_run.input_commit_sha,
        "output_commit_sha": stage_run.output_commit_sha,
        "failure": stage_run.failure,
        "created_at": stage_run.created_at.isoformat(),
        "started_at": (
            stage_run.started_at.isoformat()
            if stage_run.started_at is not None
            else None
        ),
        "lease_expires_at": (
            stage_run.lease_expires_at.isoformat()
            if stage_run.lease_expires_at is not None
            else None
        ),
        "finished_at": (
            stage_run.finished_at.isoformat()
            if stage_run.finished_at is not None
            else None
        ),
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
