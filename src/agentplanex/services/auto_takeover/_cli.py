"""Installed internal commands used by the trusted AutoCodex user proxy."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, TextIO
from uuid import uuid4

from agentplanex.bootstrap import (
    create_project_control_query,
    create_project_runtime_control,
    create_responses_transport,
)
from agentplanex.infrastructure.sqlite import SQLiteDatabase, verify_schema
from agentplanex.project_owner_agent.context.manager import OwnerContextSnapshot
from agentplanex.project_owner_agent.context.rendering import render_checkpoint
from agentplanex.project_owner_agent.models.responses import ProjectOwnerModel, ResponsesClient
from agentplanex.services.agent_invocation import AgentPromptCatalog
from agentplanex.services.historical_owner import HistoricalOwnerForkService
from agentplanex.settings import load_settings

type InputReader = Callable[[str], str]


def run_control(args: Any, *, stdout: TextIO | None = None) -> int:
    """Execute one fenced command through the real Project Runtime control graph."""
    output = stdout if stdout is not None else sys.stdout
    command = " ".join(args.action).strip()
    if not command:
        return _error("control", ValueError("Command must not be empty"), output)
    try:
        result: object
        if command == "view":
            result = create_project_control_query(project_path=args.cwd).get_current()
        else:
            settings = load_settings(args.config)
            control = create_project_runtime_control(
                project_path=args.cwd,
                approval_mode="yolo",
                settings=settings,
                mutation_fence_token=args.takeover_fence,
            )
            result = _dispatch_control(control, command)
    except Exception as error:
        return _error("control", error, output)
    _print({"action": command, "ok": True, "result": result}, output)
    return 0


def run_owner_fork(
    args: Any,
    *,
    read_input: InputReader = input,
    stdout: TextIO | None = None,
) -> int:
    """Inspect or interrogate a read-only Historical Project Owner Fork."""
    output = stdout if stdout is not None else sys.stdout
    transport = None
    try:
        settings = load_settings(args.config)
        prompts = AgentPromptCatalog(settings.runtime.prompts)
        database = SQLiteDatabase.for_project(args.cwd.resolve())
        verify_schema(database)
        forks = HistoricalOwnerForkService(database, prompts)
        if args.print_context:
            restored = forks.restore(args.message_id, summary_id=args.summary_id)
            _print(
                {
                    "action": "context",
                    "ok": True,
                    "context": _owner_context(
                        restored,
                        prompts.summary_context_header,
                    ),
                },
                output,
            )
            return 0
        model_settings = settings.project_owner_agent.selected_model
        transport = create_responses_transport(settings)
        fork = forks.open(
            args.message_id,
            summary_id=args.summary_id,
            model=ProjectOwnerModel(
                tools=None,
                responses=ResponsesClient(
                    model=model_settings.name,
                    transport=transport,
                ),
            ),
            model_name=model_settings.name,
        )
        _print(
            {
                "action": "fork-opened",
                "ok": True,
                "model": {"name": fork.model_name, "tools": []},
                "fidelity": fork.fidelity,
                "context": _owner_context(
                    fork.context,
                    prompts.summary_context_header,
                ),
            },
            output,
        )
        while True:
            try:
                question = read_input("investigator> ").strip()
            except EOFError:
                return 0
            if question in {"/exit", "/quit"}:
                return 0
            if not question:
                continue
            exchange = fork.ask(question)
            _print(
                {
                    "action": "fork-answer",
                    "ok": True,
                    "turn": exchange.turn,
                    "question": exchange.question,
                    "answer": exchange.answer,
                },
                output,
            )
    except Exception as error:
        return _error("owner-fork", error, output)
    finally:
        if transport is not None:
            transport.close()


def _dispatch_control(control: Any, command: str) -> object:
    if command.startswith("{") or command == "tool" or command.startswith("tool "):
        payload = command if command.startswith("{") else command.removeprefix("tool").strip()
        return control.execute_tool(_action(payload))
    head, separator, tail = command.partition(" ")
    message = tail.strip()
    if head == "message":
        if not message:
            raise ValueError("message content must not be empty")
        return control.submit_message(message)
    if head == "approve" and not separator:
        return control.approve_plan()
    if head == "reject":
        return control.reject_plan(message)
    if head == "start" and not separator:
        return control.start_first_run()
    if head == "approve-blocked-run" and not separator:
        return control.approve_blocked_run()
    if head == "reject-blocked-run":
        if not message:
            raise ValueError("reject-blocked-run requires feedback")
        return control.reject_blocked_run(message)
    if head == "drive-delivery" and not separator:
        raise ValueError(
            "AutoTakeover does not own drive-delivery; Dispatcher resumes Stage execution"
        )
    if head != "drive":
        return control.submit_message(command)
    mode, mode_separator, payload = message.partition(" ")
    if not mode_separator and mode in {"", "model"}:
        return control.drive_owner_model()
    if mode == "tool" and mode_separator:
        return control.drive_owner_tool(_action(payload))
    if mode == "reply" and mode_separator and payload.strip():
        return control.reply_owner(payload.strip())
    if mode == "fail" and mode_separator and payload.strip():
        return control.fail_owner(payload.strip())
    raise ValueError("drive mode must be model, tool, reply, or fail")


def _action(raw: str) -> dict[str, object]:
    try:
        parsed: object = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError(f"Tool action must be a JSON object: {error.msg}") from error
    if not isinstance(parsed, dict):
        raise ValueError("Tool action must be a JSON object")
    if not isinstance(parsed.get("tool"), str) or not str(parsed["tool"]).strip():
        raise ValueError("Tool action requires a non-empty tool")
    if not isinstance(parsed.get("arguments"), dict):
        raise ValueError("Tool action requires object arguments")
    call_id = parsed.get("call_id")
    if call_id is None:
        parsed["call_id"] = uuid4().hex
    elif not isinstance(call_id, str) or not call_id.strip():
        raise ValueError("Tool action call_id must be a non-empty string")
    return parsed


def _owner_context(
    restored: OwnerContextSnapshot,
    summary_context_header: str,
) -> dict[str, object]:
    return {
        "triage_id": restored.triage_id,
        "project_owner_session_id": restored.project_owner_session_id,
        "through": {
            "message_id": restored.through_message_id,
            "sequence": restored.through_sequence,
        },
        "summary": restored.summary,
        "system_prompt": restored.system_prompt,
        "tools": list(restored.tools),
        "messages": list(
            render_checkpoint(
                system_prompt=restored.system_prompt,
                summary=restored.summary,
                message_history=restored.message_history,
                summary_context_header=summary_context_header,
            )
        ),
    }


def _jsonable(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (Path, datetime, date)):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _print(value: object, output: TextIO) -> None:
    print(json.dumps(_jsonable(value), ensure_ascii=False, sort_keys=True), file=output)


def _error(action: str, error: Exception, output: TextIO) -> int:
    _print(
        {
            "action": action,
            "ok": False,
            "error": f"{type(error).__name__}: {error}",
        },
        output,
    )
    return 1
