"""Observable tests for the direct Tool Action debug entry point."""

import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import ClassVar

import pytest

from agentplanex.bootstrap import create_project_runtime
from agentplanex.domains import (
    ActionOutput,
    ExecutionEvent,
    Message,
    MessageHistory,
    ProjectRuntimeContext,
)
from agentplanex.infrastructure.sqlite import SQLiteDatabase
from agentplanex.infrastructure.sqlite.repositories import (
    SQLiteExecutionEventRepository,
    SQLiteMessageHistoryRepository,
    SQLiteProjectRuntimeContextRepository,
)
from agentplanex.project_owner_agent.exception import ReplyToHuman
from agentplanex.project_runtime.executions import create_project_executions
from agentplanex.services import PlanningService
from agentplanex.services import project_runtime as project_runtime_service
from agentplanex.settings import BashSettings, RuntimeSettings
from scripts import debug_tool_cli


class _ReplyingModel:
    queries: ClassVar[list[list[Message]]] = []

    def __init__(self, **_kwargs: object) -> None:
        pass

    def query(self, messages: list[Message]) -> Message:
        type(self).queries.append([dict(message) for message in messages])
        content = str(messages[-1].get("content", ""))
        raise ReplyToHuman(
            content=content,
            response={"role": "assistant", "content": content},
        )

    def format_observation_messages(
        self,
        message: Message,
        outputs: list[ActionOutput],
    ) -> list[Message]:
        raise AssertionError("Interaction tests do not execute model tool calls")


class _PlanRequestingModel:
    def __init__(self, **_kwargs: object) -> None:
        pass

    def query(self, messages: list[Message]) -> Message:
        assert messages[-1].get("role") == "user"
        return {
            "role": "assistant",
            "content": "",
            "extra": {
                "actions": [
                    {
                        "tool": "request_plan_approval",
                        "call_id": "request-plan-test",
                        "arguments": {},
                    }
                ]
            },
        }

    def format_observation_messages(
        self,
        _message: Message,
        outputs: list[ActionOutput],
    ) -> list[Message]:
        return [
            {
                "role": "tool",
                "content": "plan approval requested",
                "extra": outputs[0],
            }
        ]


def _write_specs(project_path: Path) -> None:
    for name in ("architecture.md", "requirements.md", "roadmap.md"):
        (project_path / name).write_text(f"# {name}\n", encoding="utf-8")


def _git(project_path: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(project_path), *arguments],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _load_context(project_path: Path):
    database = SQLiteDatabase.for_project(project_path)
    repository = SQLiteProjectRuntimeContextRepository()
    with database.connection() as connection:
        contexts = repository.list_all(connection)
    assert len(contexts) == 1
    return contexts[0]


def _loaded_message_contents(project_path: Path) -> list[str]:
    histories = _loaded_message_histories(project_path)
    return [
        str(message.get("content", ""))
        for history in histories
        for message in history.message
    ]


def _loaded_message_histories(project_path: Path) -> tuple[MessageHistory, ...]:
    database = SQLiteDatabase.for_project(project_path)
    repository = SQLiteMessageHistoryRepository()
    with database.connection() as connection:
        owner = connection.execute(
            "SELECT project_owner_session_id FROM project_owner_agent"
        ).fetchone()
        assert owner is not None
        histories = repository.list_by_session_id(
            connection,
            owner["project_owner_session_id"],
        )
    return histories


def _loaded_events(project_path: Path) -> tuple[ExecutionEvent, ...]:
    database = SQLiteDatabase.for_project(project_path)
    repository = SQLiteExecutionEventRepository()
    with database.connection() as connection:
        context = SQLiteProjectRuntimeContextRepository().list_all(connection)
        assert len(context) == 1
        return repository.list_by_triage_id(connection, context[0].triage_id)


def test_real_react_loop_records_timeline_with_exact_message_checkpoints(
    initialize_git_project: Callable[[], Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_path = initialize_git_project()
    _write_specs(project_path)
    monkeypatch.setattr(project_runtime_service, "JBBModel", _PlanRequestingModel)

    result = create_project_runtime(
        project_path=project_path,
        approval_mode="yolo",
    ).run("Request approval for the current plan")

    histories = _loaded_message_histories(project_path)
    events = _loaded_events(project_path)
    assert result.status.value == "PlanApprovalRequested"
    assert len(histories) == 3
    assert [event.event_type.value for event in events] == [
        "REACT_LOOP_ENTERED",
        "RUNTIME_CONTEXT_UPDATED",
        "RUNTIME_CONTEXT_UPDATED",
        "PLAN_APPROVAL_REQUESTED",
        "REACT_LOOP_EXITED",
    ]

    react_loop_id = events[0].react_loop_id
    assert react_loop_id is not None
    assert all(event.react_loop_id == react_loop_id for event in events)
    assert events[0].payload == {"task_type": "USER_INPUT"}
    assert events[0].message_id == histories[0].message_id
    assert events[1].message_id == histories[0].message_id
    assert events[1].payload == {
        "reason": "CONVERSATION_STARTED",
        "changes": {"status": {"from": "TRIAGE", "to": "TODO"}},
    }
    assert events[2].message_id == histories[1].message_id
    assert events[3].message_id == histories[1].message_id
    assert events[2].payload == {
        "reason": "PLAN_APPROVAL_REQUESTED",
        "changes": {
            "pending_action": {"from": None, "to": "PLAN_APPROVAL"}
        },
    }
    assert events[3].payload == {}
    assert events[4].message_id == histories[2].message_id
    assert events[4].payload == {
        "agent_exit_status": "PlanApprovalRequested"
    }
    assistant_action = histories[1].message[0]
    assert assistant_action["extra"]["actions"][0]["tool"] == (
        "request_plan_approval"
    )


def test_executes_project_bound_action_without_constructing_a_model(
    initialize_git_project: Callable[[], Path],
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    project_path = initialize_git_project()

    class _UnexpectedModel:
        def __init__(self, **_kwargs: object) -> None:
            raise AssertionError("tool debug entry must not construct a model")

    monkeypatch.setattr(project_runtime_service, "JBBModel", _UnexpectedModel)

    result = debug_tool_cli.main(
        [
            "--cwd",
            str(project_path),
            "--print",
            json.dumps(
                {
                    "tool": "bash",
                    "call_id": "project-bound-pwd",
                    "arguments": {"command": "pwd"},
                }
            ),
        ]
    )

    captured = capfd.readouterr()
    response = json.loads(captured.out)
    assert result == 0
    assert captured.err == ""
    assert response == {
        "call_id": "project-bound-pwd",
        "tool": "bash",
        "ok": True,
        "result": {
            "output": f"{project_path.resolve()}\n",
            "returncode": 0,
            "exception_info": "",
        },
        "exit": None,
    }
    assert (project_path / ".agentplanex" / "agentplanex.sqlite3").is_file()


def test_returns_unknown_tool_failure_as_json(
    initialize_git_project: Callable[[], Path],
    capfd: pytest.CaptureFixture[str],
) -> None:
    project_path = initialize_git_project()

    result = debug_tool_cli.main(
        [
            "--cwd",
            str(project_path),
            "--print",
            '{"tool":"missing","arguments":{}}',
        ]
    )

    response = json.loads(capfd.readouterr().out)
    assert result == 1
    assert response["tool"] == "missing"
    assert response["ok"] is False
    assert response["result"]["returncode"] == -1
    assert response["result"]["exception_info"] == "Unknown tool: 'missing'"


def test_invalid_json_does_not_create_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    def unexpected_runtime(**_kwargs: object) -> None:
        raise AssertionError("invalid input must not create a Runtime")

    monkeypatch.setattr(debug_tool_cli, "create_project_runtime", unexpected_runtime)

    result = debug_tool_cli.main(
        ["--cwd", str(tmp_path), "--print", "tool", "not-json"]
    )

    response = json.loads(capfd.readouterr().out)
    assert result == 2
    assert response["ok"] is False
    assert response["result"] is None
    assert "Tool action must be a JSON object" in response["error"]


def test_missing_specs_are_returned_as_correctable_tool_error(
    initialize_git_project: Callable[[], Path],
    capfd: pytest.CaptureFixture[str],
) -> None:
    project_path = initialize_git_project()

    result = debug_tool_cli.main(
        [
            "--cwd",
            str(project_path),
            "--print",
            '{"tool":"request_plan_approval","arguments":{}}',
        ]
    )
    response = json.loads(capfd.readouterr().out)

    assert result == 1
    assert response["result"] == {
        "ok": False,
        "error": (
            "Missing Plan specification documents: "
            "architecture.md, requirements.md, roadmap.md"
        ),
    }
    assert response["exit"] is None
    assert _load_context(project_path).pending_action is None


def test_unexpected_gate_failure_is_not_converted_to_tool_observation(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    _write_specs(project_path)
    database = SQLiteDatabase.for_project(project_path)
    context = ProjectRuntimeContext("triage-unexpected")
    contexts = SQLiteProjectRuntimeContextRepository()
    with database.transaction() as connection:
        contexts.insert(connection, context)

    def fail_unexpectedly(_documents: tuple[Path, ...]) -> None:
        raise RuntimeError("reviewer transport failed")

    planning = PlanningService(
        project_path=project_path,
        database=database,
        review_plan=fail_unexpectedly,
    )
    executions = create_project_executions(
        project_path,
        RuntimeSettings(bash=BashSettings()),
        planning,
    )

    with pytest.raises(RuntimeError, match="reviewer transport failed"):
        executions.execute(
            context,
            {"tool": "request_plan_approval", "arguments": {}},
        )


def test_request_then_approve_commits_only_specs_and_resumes_owner(
    initialize_git_project: Callable[[], Path],
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    project_path = initialize_git_project()
    _write_specs(project_path)
    initial_head = _git(project_path, "rev-parse", "HEAD")
    (project_path / "index.html").write_text("staged user work\n", encoding="utf-8")
    _git(project_path, "add", "index.html")

    request_result = debug_tool_cli.main(
        [
            "--cwd",
            str(project_path),
            "--print",
            '{"tool":"request_plan_approval","arguments":{}}',
        ]
    )
    request_response = json.loads(capfd.readouterr().out)

    assert request_result == 0
    assert request_response["ok"] is True
    assert request_response["result"]["status"] == "TODO"
    assert request_response["result"]["pending_action"] == "PLAN_APPROVAL"
    assert request_response["exit"]["status"] == "PlanApprovalRequested"
    assert _git(project_path, "rev-parse", "HEAD") == initial_head

    _ReplyingModel.queries = []
    monkeypatch.setattr(project_runtime_service, "JBBModel", _ReplyingModel)
    approve_result = debug_tool_cli.main(
        ["--cwd", str(project_path), "--print", "approve"]
    )
    approve_response = json.loads(capfd.readouterr().out)

    context = _load_context(project_path)
    assert approve_result == 0
    assert approve_response["action"] == "approve"
    assert approve_response["result"]["status"] == "ReplyToHuman"
    assert context.status == "TODO"
    assert context.pending_action is None
    assert context.current_plan_commit_sha == _git(project_path, "rev-parse", "HEAD")
    assert set(_git(project_path, "show", "--format=", "--name-only").splitlines()) == {
        "architecture.md",
        "requirements.md",
        "roadmap.md",
    }
    assert _git(project_path, "diff", "--cached", "--name-only") == "index.html"
    assert any(
        content.startswith("The user approved the current Plan.")
        for content in _loaded_message_contents(project_path)
    )
    histories = _loaded_message_histories(project_path)
    events = _loaded_events(project_path)
    assert [event.event_type.value for event in events] == [
        "RUNTIME_CONTEXT_UPDATED",
        "PLAN_APPROVAL_REQUESTED",
        "RUNTIME_CONTEXT_UPDATED",
        "PLAN_APPROVED",
        "REACT_LOOP_ENTERED",
        "REACT_LOOP_EXITED",
    ]
    assert all(event.react_loop_id is None for event in events[:4])
    assert all(event.message_id is None for event in events[:2])
    assert events[2].message_id == histories[0].message_id
    assert events[3].message_id == histories[0].message_id
    assert events[3].payload == {
        "plan_commit_sha": context.current_plan_commit_sha
    }
    assert events[4].message_id == histories[0].message_id
    assert events[4].react_loop_id is not None
    assert events[5].react_loop_id == events[4].react_loop_id
    assert events[5].message_id == histories[1].message_id


def test_request_then_reject_does_not_commit_and_resumes_owner(
    initialize_git_project: Callable[[], Path],
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    project_path = initialize_git_project()
    _write_specs(project_path)
    initial_head = _git(project_path, "rev-parse", "HEAD")
    debug_tool_cli.main(
        [
            "--cwd",
            str(project_path),
            "--print",
            '{"tool":"request_plan_approval","arguments":{}}',
        ]
    )
    capfd.readouterr()

    monkeypatch.setattr(project_runtime_service, "JBBModel", _ReplyingModel)
    reject_result = debug_tool_cli.main(
        [
            "--cwd",
            str(project_path),
            "--print",
            "reject",
            "requirements are incomplete",
        ]
    )
    reject_response = json.loads(capfd.readouterr().out)

    context = _load_context(project_path)
    assert reject_result == 0
    assert reject_response["action"] == "reject"
    assert context.status == "TODO"
    assert context.pending_action is None
    assert context.current_plan_commit_sha is None
    assert _git(project_path, "rev-parse", "HEAD") == initial_head
    assert (
        "The user rejected the current Plan. "
        "Feedback: requirements are incomplete"
    ) in _loaded_message_contents(project_path)
    histories = _loaded_message_histories(project_path)
    events = _loaded_events(project_path)
    assert [event.event_type.value for event in events] == [
        "RUNTIME_CONTEXT_UPDATED",
        "PLAN_APPROVAL_REQUESTED",
        "RUNTIME_CONTEXT_UPDATED",
        "PLAN_REJECTED",
        "REACT_LOOP_ENTERED",
        "REACT_LOOP_EXITED",
    ]
    assert events[2].message_id == histories[0].message_id
    assert events[3].message_id == histories[0].message_id
    assert events[3].payload == {}


def test_plain_text_defaults_to_message_and_starts_todo(
    initialize_git_project: Callable[[], Path],
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    project_path = initialize_git_project()
    monkeypatch.setattr(project_runtime_service, "JBBModel", _ReplyingModel)

    result = debug_tool_cli.main(
        ["--cwd", str(project_path), "--print", "please inspect the plan"]
    )
    response = json.loads(capfd.readouterr().out)

    assert result == 0
    assert response["action"] == "message"
    assert response["result"] == {
        "status": "ReplyToHuman",
        "content": "please inspect the plan",
    }
    assert _load_context(project_path).status == "TODO"
    assert _loaded_message_contents(project_path)[-2:] == [
        "please inspect the plan",
        "please inspect the plan",
    ]

    second_result = debug_tool_cli.main(
        ["--cwd", str(project_path), "--print", "continue"]
    )
    capfd.readouterr()

    assert second_result == 0
    restored_contents = [
        message.get("content") for message in _ReplyingModel.queries[-1]
    ]
    assert restored_contents[-3:] == [
        "please inspect the plan",
        "please inspect the plan",
        "continue",
    ]
