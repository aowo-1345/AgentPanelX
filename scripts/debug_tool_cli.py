"""Developer control surface for driving one real project Runtime.

This script is deliberately broader than the packaged ``agentplanex-owner`` CLI.
The packaged CLI submits a user message and immediately lets the Project Owner
model process it. This debug CLI exposes those steps separately so a developer can
inspect and drive the durable Runtime without relying on model decisions.

The input can be either:

* a raw Tool Action JSON object, executed directly outside an Owner activation; or
* an interaction command such as ``message``, ``drive tool``, ``approve``,
  ``start``, ``drive-delivery``, or ``view``.

Write commands use the privileged ``ProjectRuntimeControl`` for ``--cwd`` and
therefore exercise the same Context, Services, SQLite database, Git repository,
Tool implementations, and Delivery machinery as the normal Runtime. ``view`` uses
only the read-only projection. ``drive`` may invoke the Project Owner model and
``drive-delivery`` may invoke the Stage Executor. ``start`` specifically means
approving the first Delivery Run; it does not mean starting a TRIAGE conversation.
"""

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

from agentplanex.bootstrap import (  # noqa: E402
    create_project_control_query,
    create_project_runtime_control,
)
from agentplanex.domains import (  # noqa: E402
    Action,
    AgentExit,
    AgentExitStatus,
    OwnerActivation,
    OwnerActivationStatus,
    StageRun,
    ToolExecutionResult,
)
from agentplanex.services.delivery import (  # noqa: E402
    DeliveryDriveOutcome,
    MilestoneRunQueued,
)
from agentplanex.services.planning import PlanDecision  # noqa: E402
from agentplanex.services.project_control import ProjectControlView  # noqa: E402
from agentplanex.services.project_runtime_context import (  # noqa: E402
    ActivationDriveResult,
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


class ControlCommands(Protocol):
    """The privileged commands used by this developer control surface."""

    def submit_message(self, content: str) -> OwnerActivation: ...

    def approve_plan(self) -> PlanDecision: ...

    def reject_plan(self, feedback: str = "") -> PlanDecision: ...

    def drive_owner_model(self) -> ActivationDriveResult: ...

    def drive_owner_tool(self, action: Action) -> ToolActivationDriveResult: ...

    def reply_owner(self, content: str) -> ToolActivationDriveResult: ...

    def fail_owner(self, reason: str) -> ToolActivationDriveResult: ...

    def start_first_run(self) -> MilestoneRunQueued: ...

    def drive_delivery(self) -> DeliveryDriveOutcome: ...

    def execute_tool(self, action: Action) -> ToolExecutionResult: ...


class ControlQuery(Protocol):
    """The independently composed read-only control projection."""

    def get_current(self) -> ProjectControlView: ...


@dataclass(frozen=True, slots=True)
class _Interaction:
    """One parsed control command that is not a raw Tool Action."""

    action: InteractionAction
    message: str = ""
    tool_action: Action | None = None


def main(argv: Sequence[str] | None = None) -> int:
    """Create control lazily for ``--cwd`` and run one or many debug commands."""

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

    if command is not None:
        if isinstance(command, _Interaction) and command.action == "view":
            try:
                query = create_project_control_query(project_path=args.cwd)
            except ValueError as error:
                _print_error(str(error))
                return 2
            return _show_view(query)
        try:
            control = create_project_runtime_control(
                project_path=args.cwd,
                approval_mode="yolo",
            )
        except ValueError as error:
            _print_error(str(error))
            return 2
        query = (
            create_project_control_query(project_path=args.cwd)
            if isinstance(command, _Interaction)
            and command.action == "drive-delivery"
            else None
        )
        return _dispatch(control, command, query=query)
    return _run_interactive(args.cwd, action_text)


def _execute_once(
    execute_tool: ToolRunner,
    action: Action,
    *,
    stdout: TextIO | None = None,
) -> int:
    """Execute one Tool Action directly, without claiming an Owner activation."""

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
    project_path: Path,
    initial_action: str = "",
    *,
    read_input: InputReader = input,
    stdout: TextIO | None = None,
) -> int:
    """Read and dispatch commands until EOF or an explicit exit command."""

    action_text = initial_action
    control: ControlCommands | None = None
    query: ControlQuery | None = None
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
            try:
                if isinstance(command, _Interaction) and command.action == "view":
                    if query is None:
                        query = create_project_control_query(project_path=project_path)
                    _show_view(query, stdout=stdout)
                else:
                    if control is None:
                        control = create_project_runtime_control(
                            project_path=project_path,
                            approval_mode="yolo",
                        )
                    if (
                        isinstance(command, _Interaction)
                        and command.action == "drive-delivery"
                        and query is None
                    ):
                        query = create_project_control_query(project_path=project_path)
                    _dispatch(control, command, query=query, stdout=stdout)
            except ValueError as error:
                _print_error(str(error), output=stdout)
        action_text = ""


def _dispatch(
    control: ControlCommands,
    command: Action | _Interaction,
    *,
    query: ControlQuery | None = None,
    stdout: TextIO | None = None,
) -> int:
    """Route one parsed command to the privileged control interface."""

    if not isinstance(command, _Interaction):
        return _execute_once(control.execute_tool, command, stdout=stdout)
    if command.action == "message":
        return _submit_message(control, command.message, stdout=stdout)
    if command.action == "approve":
        return _submit_plan_decision(control, "approve", "", stdout=stdout)
    if command.action == "reject":
        return _submit_plan_decision(
            control,
            "reject",
            command.message,
            stdout=stdout,
        )
    if command.action == "drive":
        return _drive_once(control, stdout=stdout)
    if command.action == "drive-tool":
        if command.tool_action is None:
            raise RuntimeError("Tool drive is missing its Tool Action")
        return _drive_tool_once(control, command.tool_action, stdout=stdout)
    if command.action == "drive-reply":
        return _drive_reply_once(control, command.message, stdout=stdout)
    if command.action == "drive-fail":
        return _drive_fail_once(control, command.message, stdout=stdout)
    if command.action == "start":
        return _start_first_run(control, stdout=stdout)
    if command.action == "drive-delivery":
        if query is None:
            raise RuntimeError("Delivery drive requires its read-only projection")
        return _drive_delivery_once(control, query, stdout=stdout)
    if command.action == "view":
        raise AssertionError("View must be routed through the read-only query")
    raise AssertionError(f"Unhandled interaction action: {command.action}")


def _parse_command(command_text: str) -> Action | _Interaction:
    """Parse raw Tool JSON or one of the supported human-readable commands.

    ``tool {...}`` and a bare ``{...}`` execute outside an activation. In contrast,
    ``drive tool {...}`` claims the current Triage's unfinished activation and uses
    the supplied Tool Action as the next Project Owner step.
    """

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
    """Validate a concrete Tool Action and add a call ID when one is omitted."""

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
    control: ControlCommands,
    message: str,
    *,
    stdout: TextIO | None = None,
) -> int:
    """Persist user input and enqueue an activation without driving it."""

    output = stdout if stdout is not None else sys.stdout
    try:
        activation = control.submit_message(message)
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
    control: ControlCommands,
    action: Literal["approve", "reject"],
    feedback: str,
    *,
    stdout: TextIO | None = None,
) -> int:
    """Apply a user Plan decision and enqueue its resulting Owner activation."""

    output = stdout if stdout is not None else sys.stdout
    try:
        decision = (
            control.approve_plan()
            if action == "approve"
            else control.reject_plan(feedback)
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
                    "status": decision.state.status,
                    "pending_action": decision.state.pending_action,
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
    control: ControlCommands,
    *,
    stdout: TextIO | None = None,
) -> int:
    """Let the configured Project Owner model consume one pending activation."""

    output = stdout if stdout is not None else sys.stdout
    try:
        driven = control.drive_owner_model()
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
    control: ControlCommands,
    action: Action,
    *,
    stdout: TextIO | None = None,
) -> int:
    """Use a developer-supplied Tool Action as the next Owner activation step."""

    output = stdout if stdout is not None else sys.stdout
    try:
        driven = control.drive_owner_tool(action)
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
    control: ControlCommands,
    content: str,
    *,
    stdout: TextIO | None = None,
) -> int:
    """Finish the current Tool-driven activation with an Owner reply."""

    return _finish_tool_drive(
        "reply",
        lambda: control.reply_owner(content),
        stdout=stdout,
    )


def _drive_fail_once(
    control: ControlCommands,
    reason: str,
    *,
    stdout: TextIO | None = None,
) -> int:
    """Finish the current Tool-driven activation as a manual failure."""

    return _finish_tool_drive(
        "fail",
        lambda: control.fail_owner(reason),
        stdout=stdout,
    )


def _finish_tool_drive(
    step: Literal["reply", "fail"],
    finish: Callable[[], ToolActivationDriveResult],
    *,
    stdout: TextIO | None = None,
) -> int:
    """Render the terminal result of a manually driven Owner activation."""

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
    control: ControlCommands,
    *,
    stdout: TextIO | None = None,
) -> int:
    """Apply the user's first-Run approval and queue its first Delivery Stage."""

    output = stdout if stdout is not None else sys.stdout
    try:
        queued = control.start_first_run()
    except Exception as error:
        _print_command_error("start", error, output)
        return 1
    print(
        json.dumps(
            {
                "action": "start",
                "ok": True,
                "result": {
                    "status": queued.state.status,
                    "run_id": queued.run_id,
                    "stage_run_id": queued.stage_run_id,
                    "snapshot_id": queued.snapshot_id,
                    "milestone_key": queued.milestone_key,
                    "stage_key": queued.stage_key,
                    "input_commit_sha": queued.input_commit_sha,
                },
            },
            ensure_ascii=False,
        ),
        file=output,
    )
    return 0


def _drive_delivery_once(
    control: ControlCommands,
    query: ControlQuery,
    *,
    stdout: TextIO | None = None,
) -> int:
    """Run at most one queued Delivery Stage through the real Delivery Driver."""

    output = stdout if stdout is not None else sys.stdout
    try:
        before = query.get_current()
        active_stage = next(
            (
                stage_run
                for stage_run in reversed(before.stage_runs)
                if stage_run.status.value in {"QUEUED", "RUNNING"}
            ),
            None,
        )
        outcome = control.drive_delivery()
        after = query.get_current()
        driven_stage = (
            next(
                (
                    stage_run
                    for stage_run in after.stage_runs
                    if stage_run.stage_run_id == active_stage.stage_run_id
                ),
                None,
            )
            if active_stage is not None
            else None
        )
    except Exception as error:
        _print_command_error("drive-delivery", error, output)
        return 1
    print(
        json.dumps(
            {
                "action": "drive-delivery",
                "ok": True,
                "result": {
                    "outcome": outcome.value,
                    "context_status": after.state.status,
                    "candidate_commit_sha": after.state.current_candidate_commit_sha,
                    "stage_run": (
                        _stage_run_json(driven_stage)
                        if driven_stage is not None
                        else None
                    ),
                },
                "activation": (
                    _activation_json(after.owner_activation)
                    if after.owner_activation is not None
                    else None
                ),
            },
            ensure_ascii=False,
        ),
        file=output,
    )
    return 0


def _show_view(
    query: ControlQuery,
    *,
    stdout: TextIO | None = None,
) -> int:
    """Render the Runtime, Git, activation, Delivery, and Timeline read model."""

    output = stdout if stdout is not None else sys.stdout
    try:
        view = query.get_current()
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
    context = view.state
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
