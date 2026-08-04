"""Observable tests for the direct Tool Action debug entry point."""

import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import ClassVar

import pytest

from agentplanex.domains import (
    ActionOutput,
    ExecutionEvent,
    Message,
    MessageHistory,
    OwnerActivationStatus,
    ProjectRuntimeContext,
)
from agentplanex.infrastructure.agent_workspace import AgentWorkspaceStore
from agentplanex.infrastructure.codex import (
    CodexTransportTimeout,
    CodexTurnRequest,
    CodexTurnResult,
    CodexTurnTransport,
)
from agentplanex.infrastructure.sqlite import SQLiteDatabase
from agentplanex.infrastructure.sqlite.repositories import (
    SQLiteExecutionEventRepository,
    SQLiteMessageHistoryRepository,
    SQLiteOwnerActivationRepository,
    SQLiteProjectRuntimeContextRepository,
)
from agentplanex.project_owner_agent.exception import ReplyToHuman
from agentplanex.project_runtime.executions import create_project_executions
from agentplanex.services import PlanningService
from agentplanex.services import project_owner as project_owner_service
from agentplanex.services.planning import PlanReviewRequest, PlanReviewResult
from agentplanex.settings import BashSettings, RuntimeSettings
from scripts import debug_tool_cli


@pytest.fixture(autouse=True)
def deterministic_codex_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep Runtime/Outbox tests deterministic while exercising the real boundary."""

    def run(_self: CodexTurnTransport, request: CodexTurnRequest) -> CodexTurnResult:
        pending = tuple(
            directory / "result.json"
            for directory in request.workspace.glob("outbox/*")
            if not (directory / "result.json").exists()
        )
        is_gate = "This invocation is a protected Plan Hard Gate." in request.developer_instructions
        is_task = "Task Contract" in request.message
        is_stage = "This is a fixed AgentPlaneX Stage Contract." in request.message
        if is_stage:
            contract = json.loads(request.message.split("\n\n", 2)[1])
            assert isinstance(contract, dict)
            delivery_document = contract["delivery_document"]
            stage = contract["stage"]
            assert isinstance(delivery_document, str)
            assert isinstance(stage, dict)
            stage_key = stage["key"]
            assert isinstance(stage_key, str)
            if "FAIL_STAGE_CONTRACT" not in request.message:
                document_path = request.workspace / delivery_document
                document_path.parent.mkdir(parents=True, exist_ok=True)
                document_path.write_text(
                    f"# Delivery {stage_key}\n\nValidated deterministic Stage output.\n",
                    encoding="utf-8",
                )
                implementation = request.workspace / "src" / f"{stage_key}.txt"
                implementation.parent.mkdir(parents=True, exist_ok=True)
                implementation.write_text(
                    f"implemented {stage_key}\n",
                    encoding="utf-8",
                )
        if is_gate or is_task:
            assert len(pending) == 1
            result_path = pending[0]
            document_name = "review.md" if is_gate or "reviewer" in request.message else "plan.md"
            document_path = request.workspace / "documents" / document_name
            instruction = request.message.split("\n\n", 1)[0]
            document_path.write_text(
                f"# {document_name}\n\n{instruction}\n",
                encoding="utf-8",
            )
            if is_gate:
                digest = request.message.split(
                    "The Runtime-computed subject digest is: ", 1
                )[1].split("\n", 1)[0]
                requires_changes = any(
                    "NEEDS_REVIEW_CHANGES" in path.read_text(encoding="utf-8")
                    for _, path in request.mentions
                )
                payload: dict[str, object] = {
                    "version": 1,
                    "subject_digest": digest,
                    "decision": "revise" if requires_changes else "pass",
                    "summary": "Deterministic Plan review.",
                    "required_changes": (
                        ["Address the marked missing requirement."]
                        if requires_changes
                        else []
                    ),
                    "artifacts": [
                        {
                            "path": "documents/review.md",
                            "media_type": "text/markdown",
                        }
                    ],
                }
            else:
                payload = {
                    "version": 1,
                    "summary": "Deterministic Agent task.",
                    "artifacts": [
                        {
                            "path": f"documents/{document_name}",
                            "media_type": "text/markdown",
                        }
                    ],
                }
            result_path.write_text(json.dumps(payload), encoding="utf-8")
        return CodexTurnResult(
            thread_id=request.thread_id or "deterministic-thread",
            turn_id="deterministic-turn",
            status="completed",
            final_response=json.dumps(
                {
                    "summary": (
                        "Deterministic Stage execution."
                        if is_stage
                        else "Deterministic Codex response."
                    )
                }
            ),
        )

    monkeypatch.setattr(CodexTurnTransport, "run", run)


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


def _loaded_activations(project_path: Path):
    database = SQLiteDatabase.for_project(project_path)
    repository = SQLiteOwnerActivationRepository()
    with database.connection() as connection:
        context = SQLiteProjectRuntimeContextRepository().list_all(connection)
        assert len(context) == 1
        return repository.list_by_triage_id(connection, context[0].triage_id)


def _approve_current_plan(
    project_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> str:
    _write_specs(project_path)
    request_code = debug_tool_cli.main(
        [
            "--cwd",
            str(project_path),
            "--print",
            '{"tool":"request_plan_approval","arguments":{}}',
        ]
    )
    request = json.loads(capfd.readouterr().out)
    assert request_code == 0
    assert request["result"]["accepted"] is True

    approve_code = debug_tool_cli.main(
        ["--cwd", str(project_path), "--print", "approve"]
    )
    approval = json.loads(capfd.readouterr().out)
    assert approve_code == 0
    commit_sha = approval["result"]["plan_commit_sha"]
    assert isinstance(commit_sha, str)

    monkeypatch.setattr(project_owner_service, "JBBModel", _ReplyingModel)
    drive_code = debug_tool_cli.main(
        ["--cwd", str(project_path), "--print", "drive"]
    )
    driven = json.loads(capfd.readouterr().out)
    assert drive_code == 0
    assert driven["activation"]["status"] == "COMPLETED"
    return commit_sha


def _publish_milestones(
    project_path: Path,
    capfd: pytest.CaptureFixture[str],
    *,
    stages: list[dict[str, str]],
) -> dict[str, object]:
    code = debug_tool_cli.main(
        [
            "--cwd",
            str(project_path),
            "--print",
            json.dumps(
                {
                    "tool": "update_milestones",
                    "arguments": {
                        "reason": "Publish the complete delivery view.",
                        "milestones": [
                            {
                                "key": "milestone-1",
                                "objective": "Deliver the requested behavior.",
                                "state": "pending",
                                "stages": stages,
                            }
                        ],
                    },
                }
            ),
        ]
    )
    response = json.loads(capfd.readouterr().out)
    assert code == 0
    assert response["result"]["accepted"] is True
    result = response["result"]
    assert isinstance(result, dict)
    return result


def _request_and_start_first_run(
    project_path: Path,
    capfd: pytest.CaptureFixture[str],
) -> dict[str, object]:
    request_code = debug_tool_cli.main(
        [
            "--cwd",
            str(project_path),
            "--print",
            '{"tool":"run_next_milestone","arguments":{}}',
        ]
    )
    requested = json.loads(capfd.readouterr().out)
    assert request_code == 0
    assert requested["result"]["state"] == "FIRST_RUN_APPROVAL_REQUESTED"
    assert requested["exit"]["status"] == "FirstRunApprovalRequested"

    start_code = debug_tool_cli.main(
        ["--cwd", str(project_path), "--print", "start"]
    )
    started = json.loads(capfd.readouterr().out)
    assert start_code == 0
    result = started["result"]
    assert isinstance(result, dict)
    return result


def test_real_react_loop_records_timeline_with_exact_message_checkpoints(
    initialize_git_project: Callable[[], Path],
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    project_path = initialize_git_project()
    _write_specs(project_path)
    monkeypatch.setattr(project_owner_service, "JBBModel", _PlanRequestingModel)

    submit_result = debug_tool_cli.main(
        [
            "--cwd",
            str(project_path),
            "--print",
            "Request approval for the current plan",
        ]
    )
    submit_response = json.loads(capfd.readouterr().out)

    assert submit_result == 0
    assert submit_response["activation"]["status"] == "PENDING"
    assert [event.event_type.value for event in _loaded_events(project_path)] == [
        "RUNTIME_CONTEXT_UPDATED"
    ]

    blocked_result = debug_tool_cli.main(
        ["--cwd", str(project_path), "--print", "message", "too soon"]
    )
    blocked_response = json.loads(capfd.readouterr().out)
    assert blocked_result == 1
    assert "unfinished activation" in blocked_response["error"]
    assert len(_loaded_message_histories(project_path)) == 1
    assert len(_loaded_activations(project_path)) == 1

    drive_result = debug_tool_cli.main(
        ["--cwd", str(project_path), "--print", "drive"]
    )
    drive_response = json.loads(capfd.readouterr().out)

    histories = _loaded_message_histories(project_path)
    events = _loaded_events(project_path)
    reviewed_digest = _load_context(project_path).pending_plan_subject_digest
    assert reviewed_digest is not None
    assert drive_result == 0
    assert drive_response["result"]["status"] == "PlanApprovalRequested"
    assert drive_response["activation"]["status"] == "COMPLETED"
    assert len(histories) == 3
    assert [event.event_type.value for event in events] == [
        "RUNTIME_CONTEXT_UPDATED",
        "REACT_LOOP_ENTERED",
        "AGENT_INVOCATION_STARTED",
        "AGENT_INVOCATION_COMPLETED",
        "RUNTIME_CONTEXT_UPDATED",
        "PLAN_APPROVAL_REQUESTED",
        "REACT_LOOP_EXITED",
    ]

    assert events[0].react_loop_id is None
    assert events[0].message_id == histories[0].message_id
    assert events[0].payload == {
        "reason": "CONVERSATION_STARTED",
        "changes": {"status": {"from": "TRIAGE", "to": "TODO"}},
    }
    react_loop_id = events[1].react_loop_id
    assert react_loop_id is not None
    assert all(event.react_loop_id == react_loop_id for event in events[1:])
    assert events[1].payload == {"task_type": "USER_INPUT"}
    assert events[1].message_id == histories[0].message_id
    invocation_id = events[2].payload["invocation_id"]
    assert isinstance(invocation_id, str)
    assert events[2].payload == {
        "invocation_id": invocation_id,
        "operation": "plan_hard_gate",
        "subject_digest": reviewed_digest,
    }
    assert events[3].payload["invocation_id"] == invocation_id
    assert events[3].payload["operation"] == "plan_hard_gate"
    assert events[3].payload["subject_digest"] == reviewed_digest
    assert events[3].payload["decision"] == "pass"
    assert events[3].payload["required_change_count"] == 0
    assert events[2].message_id == histories[1].message_id
    assert events[3].message_id == histories[1].message_id
    assert events[4].message_id == histories[1].message_id
    assert events[5].message_id == histories[1].message_id
    assert events[4].payload == {
        "reason": "PLAN_APPROVAL_REQUESTED",
        "changes": {
            "pending_action": {"from": None, "to": "PLAN_APPROVAL"},
            "pending_plan_subject_digest": {
                "from": None,
                "to": reviewed_digest,
            },
        },
    }
    assert events[5].payload == {}
    assert events[6].message_id == histories[2].message_id
    assert events[6].payload == {
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

    monkeypatch.setattr(project_owner_service, "JBBModel", _UnexpectedModel)

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


def test_talk_task_keeps_workspace_and_publishes_document_uri(
    initialize_git_project: Callable[[], Path],
    capfd: pytest.CaptureFixture[str],
) -> None:
    project_path = initialize_git_project()
    (project_path / "requirements.md").write_text(
        "# Requirements\n\nShip a durable planner workspace.\n",
        encoding="utf-8",
    )
    _git(project_path, "add", "requirements.md")
    _git(project_path, "commit", "-m", "Add requirements")

    debug_tool_cli.main(
        [
            "--cwd",
            str(project_path),
            "--print",
            '{"tool":"bash","arguments":{"command":"pwd"}}',
        ]
    )
    capfd.readouterr()
    context_before = _load_context(project_path)
    initial_head = _git(project_path, "rev-parse", "HEAD")

    first_code = debug_tool_cli.main(
        [
            "--cwd",
            str(project_path),
            "--print",
            json.dumps(
                {
                    "tool": "talk_to_agent",
                    "arguments": {
                        "agent_id": "planner",
                        "kind": "task",
                        "message": "Create the initial Plan document.",
                        "artifacts": [{"uri": "project:///requirements.md"}],
                    },
                }
            ),
        ]
    )
    first_response = json.loads(capfd.readouterr().out)
    first_result = first_response["result"]

    assert first_code == 0
    assert first_result["ok"] is True
    assert first_result["agent_id"] == "planner"
    assert len(first_result["artifacts"]) == 1
    first_artifact = first_result["artifacts"][0]
    assert first_artifact["uri"].startswith(
        "artifact://local/agent-workspaces/"
    )
    store = AgentWorkspaceStore(project_path, 65_536, 262_144)
    first_document = store.resolve_artifact(first_artifact["uri"])
    assert "Create the initial Plan document." in first_document.path.read_text(
        encoding="utf-8"
    )

    second_code = debug_tool_cli.main(
        [
            "--cwd",
            str(project_path),
            "--print",
            json.dumps(
                {
                    "tool": "talk_to_agent",
                    "arguments": {
                        "agent_id": "planner",
                        "kind": "task",
                        "message": "Refine the existing Plan document.",
                        "conversation_id": first_result["conversation_id"],
                        "artifacts": [],
                    },
                }
            ),
        ]
    )
    second_response = json.loads(capfd.readouterr().out)
    second_result = second_response["result"]

    assert second_code == 0
    assert second_result["conversation_id"] == first_result["conversation_id"]
    assert second_result["artifacts"][0]["uri"] == first_artifact["uri"]
    assert second_result["artifacts"][0]["sha256"] != first_artifact["sha256"]
    assert "Refine the existing Plan document." in first_document.path.read_text(
        encoding="utf-8"
    )
    workspace = first_document.path.parents[1]
    assert len(tuple(workspace.glob("outbox/*/result.json"))) == 2

    cross_agent_code = debug_tool_cli.main(
        [
            "--cwd",
            str(project_path),
            "--print",
            json.dumps(
                {
                    "tool": "talk_to_agent",
                    "arguments": {
                        "agent_id": "reviewer",
                        "kind": "message",
                        "message": "Continue the Planner conversation.",
                        "conversation_id": first_result["conversation_id"],
                        "artifacts": [],
                    },
                }
            ),
        ]
    )
    cross_agent_response = json.loads(capfd.readouterr().out)
    assert cross_agent_code == 1
    assert "different Agent" in cross_agent_response["result"]["error"]
    events = _loaded_events(project_path)
    assert [event.event_type.value for event in events] == [
        "AGENT_INVOCATION_STARTED",
        "AGENT_INVOCATION_COMPLETED",
        "AGENT_INVOCATION_STARTED",
        "AGENT_INVOCATION_COMPLETED",
        "AGENT_INVOCATION_STARTED",
        "AGENT_INVOCATION_FAILED",
    ]
    first_invocation = events[0].payload["invocation_id"]
    second_invocation = events[2].payload["invocation_id"]
    failed_invocation = events[4].payload["invocation_id"]
    assert len({first_invocation, second_invocation, failed_invocation}) == 3
    assert events[1].payload["invocation_id"] == first_invocation
    assert events[3].payload["invocation_id"] == second_invocation
    assert events[5].payload["invocation_id"] == failed_invocation
    assert events[0].payload == {
        "invocation_id": first_invocation,
        "operation": "talk_to_agent",
        "agent_id": "planner",
        "kind": "task",
        "resumed": False,
        "input_artifact_count": 1,
    }
    assert events[1].payload["output_artifacts"] == [first_artifact]
    assert events[2].payload["resumed"] is True
    assert events[3].payload["output_artifacts"] == [second_result["artifacts"][0]]
    assert events[5].payload["failure_type"] == "AgentCollaborationError"
    assert _load_context(project_path) == context_before
    assert _git(project_path, "rev-parse", "HEAD") == initial_head
    assert _git(project_path, "status", "--short") == ""


def test_plan_hard_gate_revise_returns_review_without_transition(
    initialize_git_project: Callable[[], Path],
    capfd: pytest.CaptureFixture[str],
) -> None:
    project_path = initialize_git_project()
    _write_specs(project_path)
    (project_path / "requirements.md").write_text(
        "# Requirements\n\nNEEDS_REVIEW_CHANGES\n",
        encoding="utf-8",
    )
    initial_head = _git(project_path, "rev-parse", "HEAD")

    code = debug_tool_cli.main(
        [
            "--cwd",
            str(project_path),
            "--print",
            '{"tool":"request_plan_approval","arguments":{}}',
        ]
    )
    response = json.loads(capfd.readouterr().out)
    result = response["result"]

    assert code == 0
    assert result["ok"] is True
    assert result["accepted"] is False
    assert result["review"]["decision"] == "revise"
    assert result["review"]["required_changes"]
    assert response["exit"] is None
    review = AgentWorkspaceStore(project_path, 65_536, 262_144).resolve_artifact(
        result["review"]["artifact"]["uri"]
    )
    assert review.path.name == "review.md"
    context = _load_context(project_path)
    assert context.pending_action is None
    assert context.pending_plan_subject_digest is None
    assert _git(project_path, "rev-parse", "HEAD") == initial_head
    events = _loaded_events(project_path)
    assert [event.event_type.value for event in events] == [
        "AGENT_INVOCATION_STARTED",
        "AGENT_INVOCATION_COMPLETED",
    ]
    invocation_id = events[0].payload["invocation_id"]
    assert events[1].payload["invocation_id"] == invocation_id
    assert events[1].payload["decision"] == "revise"
    assert events[1].payload["required_change_count"] == 1
    assert events[1].payload["review_artifact"] == result["review"]["artifact"]


def test_plan_hard_gate_timeout_records_failed_invocation_only(
    initialize_git_project: Callable[[], Path],
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    project_path = initialize_git_project()
    _write_specs(project_path)

    def timeout(
        _self: CodexTurnTransport,
        _request: CodexTurnRequest,
    ) -> CodexTurnResult:
        raise CodexTransportTimeout("timed out")

    monkeypatch.setattr(CodexTurnTransport, "run", timeout)
    code = debug_tool_cli.main(
        [
            "--cwd",
            str(project_path),
            "--print",
            '{"tool":"request_plan_approval","arguments":{}}',
        ]
    )
    response = json.loads(capfd.readouterr().out)

    assert code == 1
    assert response["result"]["ok"] is False
    assert "timed out" in response["result"]["error"]
    events = _loaded_events(project_path)
    assert [event.event_type.value for event in events] == [
        "AGENT_INVOCATION_STARTED",
        "AGENT_INVOCATION_FAILED",
    ]
    assert events[1].payload["invocation_id"] == events[0].payload["invocation_id"]
    assert events[1].payload["operation"] == "plan_hard_gate"
    assert events[1].payload["failure_type"] == "PlanningError"
    context = _load_context(project_path)
    assert context.pending_action is None
    assert context.pending_plan_subject_digest is None


def test_plan_approval_rejects_specs_changed_after_hard_gate(
    initialize_git_project: Callable[[], Path],
    capfd: pytest.CaptureFixture[str],
) -> None:
    project_path = initialize_git_project()
    _write_specs(project_path)
    initial_head = _git(project_path, "rev-parse", "HEAD")

    request_code = debug_tool_cli.main(
        [
            "--cwd",
            str(project_path),
            "--print",
            '{"tool":"request_plan_approval","arguments":{}}',
        ]
    )
    request_response = json.loads(capfd.readouterr().out)
    assert request_code == 0
    assert request_response["result"]["accepted"] is True

    (project_path / "requirements.md").write_text(
        "# requirements.md\n\nChanged after review.\n",
        encoding="utf-8",
    )
    approve_code = debug_tool_cli.main(
        ["--cwd", str(project_path), "--print", "approve"]
    )
    approve_response = json.loads(capfd.readouterr().out)

    assert approve_code == 1
    assert approve_response["ok"] is False
    assert "changed after Hard Gate review" in approve_response["error"]
    context = _load_context(project_path)
    assert context.pending_action == "PLAN_APPROVAL"
    assert context.pending_plan_subject_digest is not None
    assert context.current_plan_commit_sha is None
    assert _git(project_path, "rev-parse", "HEAD") == initial_head


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

    def fail_unexpectedly(_request: PlanReviewRequest) -> PlanReviewResult:
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


def test_complete_rolling_delivery_is_observable_through_debug_commands(
    initialize_git_project: Callable[[], Path],
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    project_path = initialize_git_project()
    plan_commit_sha = _approve_current_plan(project_path, monkeypatch, capfd)
    published = _publish_milestones(
        project_path,
        capfd,
        stages=[
            {"key": "stage-1", "objective": "Implement the first part."},
            {"key": "stage-2", "objective": "Implement the second part."},
        ],
    )
    initial_snapshot_id = published["snapshot"]["snapshot_id"]

    view_code = debug_tool_cli.main(
        ["--cwd", str(project_path), "--print", "view"]
    )
    published_view = json.loads(capfd.readouterr().out)["view"]
    assert view_code == 0
    assert published_view["context"]["status"] == "TODO"
    assert published_view["snapshot"]["snapshot_id"] == initial_snapshot_id
    assert published_view["snapshot"]["milestones"][0]["state"] == "pending"
    assert published_view["stage_runs"] == []

    started = _request_and_start_first_run(project_path, capfd)
    run_id = started["run_id"]
    assert isinstance(run_id, str)
    assert started["input_commit_sha"] == plan_commit_sha
    assert _git(project_path, "rev-parse", "HEAD") == plan_commit_sha

    first_code = debug_tool_cli.main(
        ["--cwd", str(project_path), "--print", "drive-delivery"]
    )
    first = json.loads(capfd.readouterr().out)
    assert first_code == 0
    assert first["result"]["outcome"] == "stage_succeeded"
    assert first["result"]["stage_run"]["stage_key"] == "stage-1"
    assert first["result"]["stage_run"]["status"] == "SUCCEEDED"
    assert first["activation"] is None
    assert _git(project_path, "rev-parse", "HEAD") == plan_commit_sha

    mid_code = debug_tool_cli.main(
        ["--cwd", str(project_path), "--print", "view"]
    )
    mid_view = json.loads(capfd.readouterr().out)["view"]
    assert mid_code == 0
    assert mid_view["context"]["current_stage_key"] == "stage-2"
    assert [stage_run["status"] for stage_run in mid_view["stage_runs"]] == [
        "SUCCEEDED",
        "QUEUED",
    ]
    assert mid_view["allowed_actions"] == ["drive-delivery"]

    second_code = debug_tool_cli.main(
        ["--cwd", str(project_path), "--print", "drive-delivery"]
    )
    second = json.loads(capfd.readouterr().out)
    candidate_commit_sha = second["result"]["candidate_commit_sha"]
    assert second_code == 0
    assert second["result"]["outcome"] == "candidate_ready"
    assert isinstance(candidate_commit_sha, str)
    assert second["activation"]["task_type"] == "EXECUTION_RESULT"
    assert second["activation"]["status"] == "PENDING"
    assert _git(project_path, "rev-parse", "HEAD") == plan_commit_sha
    assert _git(
        project_path,
        "rev-parse",
        f"refs/agentplanex/candidates/{run_id}",
    ) == candidate_commit_sha
    assert "Validated deterministic Stage output." in _git(
        project_path,
        "show",
        (
            f"{candidate_commit_sha}:docs/agentplanex/deliveries/"
            f"{run_id}/stage-2.md"
        ),
    )

    candidate_view_code = debug_tool_cli.main(
        ["--cwd", str(project_path), "--print", "view"]
    )
    candidate_view = json.loads(capfd.readouterr().out)["view"]
    assert candidate_view_code == 0
    assert candidate_view["context"]["current_candidate_commit_sha"] == (
        candidate_commit_sha
    )
    assert candidate_view["allowed_actions"] == ["drive"]

    activation_code = debug_tool_cli.main(
        ["--cwd", str(project_path), "--print", "drive"]
    )
    activation = json.loads(capfd.readouterr().out)
    assert activation_code == 0
    assert activation["activation"]["task_type"] == "EXECUTION_RESULT"
    assert activation["activation"]["status"] == "COMPLETED"

    decision_code = debug_tool_cli.main(
        [
            "--cwd",
            str(project_path),
            "--print",
            json.dumps(
                {
                    "tool": "decide_milestone_candidate",
                    "arguments": {
                        "decision": "accept",
                        "reason": "The Candidate satisfies the Milestone.",
                    },
                }
            ),
        ]
    )
    decision = json.loads(capfd.readouterr().out)
    assert decision_code == 0
    assert decision["result"]["completed"] is True
    assert decision["result"]["status"] == "DONE"
    assert decision["exit"]["status"] == "TriageDevelopmentCompleted"
    assert _git(project_path, "rev-parse", "HEAD") == candidate_commit_sha

    final_code = debug_tool_cli.main(
        ["--cwd", str(project_path), "--print", "view"]
    )
    final_view = json.loads(capfd.readouterr().out)["view"]
    assert final_code == 0
    assert final_view["context"]["status"] == "DONE"
    assert final_view["context"]["current_run_id"] is None
    assert final_view["context"]["current_candidate_commit_sha"] is None
    assert final_view["snapshot"]["previous_snapshot_id"] == initial_snapshot_id
    assert final_view["snapshot"]["milestones"][0]["state"] == "completed"
    assert [stage_run["status"] for stage_run in final_view["stage_runs"]] == [
        "SUCCEEDED",
        "SUCCEEDED",
    ]
    assert final_view["git"]["head"] == candidate_commit_sha

    event_types = [event.event_type.value for event in _loaded_events(project_path)]
    for event_type in (
        "MILESTONES_UPDATED",
        "FIRST_RUN_APPROVAL_REQUESTED",
        "MILESTONE_RUN_QUEUED",
        "CANDIDATE_READY",
        "CANDIDATE_ACCEPTED",
        "TRIAGE_DEVELOPMENT_COMPLETED",
    ):
        assert event_type in event_types
    assert event_types.count("STAGE_RUN_STARTED") == 2
    assert event_types.count("STAGE_RUN_SUCCEEDED") == 2


def test_rejected_candidate_stays_reachable_and_next_run_skips_first_approval(
    initialize_git_project: Callable[[], Path],
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    project_path = initialize_git_project()
    target_commit_sha = _approve_current_plan(project_path, monkeypatch, capfd)
    _publish_milestones(
        project_path,
        capfd,
        stages=[{"key": "stage-1", "objective": "Implement a reviewable change."}],
    )
    started = _request_and_start_first_run(project_path, capfd)
    run_id = started["run_id"]
    assert isinstance(run_id, str)

    drive_code = debug_tool_cli.main(
        ["--cwd", str(project_path), "--print", "drive-delivery"]
    )
    driven = json.loads(capfd.readouterr().out)
    candidate_commit_sha = driven["result"]["candidate_commit_sha"]
    assert drive_code == 0
    assert isinstance(candidate_commit_sha, str)
    debug_tool_cli.main(["--cwd", str(project_path), "--print", "drive"])
    capfd.readouterr()

    reject_code = debug_tool_cli.main(
        [
            "--cwd",
            str(project_path),
            "--print",
            json.dumps(
                {
                    "tool": "decide_milestone_candidate",
                    "arguments": {
                        "decision": "reject",
                        "reason": "The Candidate needs a different implementation.",
                    },
                }
            ),
        ]
    )
    rejected = json.loads(capfd.readouterr().out)
    assert reject_code == 0
    assert rejected["result"]["decision"] == "reject"
    assert rejected["result"]["status"] == "IN_PROGRESS"
    assert rejected["result"]["completed"] is False
    assert _git(project_path, "rev-parse", "HEAD") == target_commit_sha
    assert _git(
        project_path,
        "rev-parse",
        f"refs/agentplanex/candidates/{run_id}",
    ) == candidate_commit_sha

    next_code = debug_tool_cli.main(
        [
            "--cwd",
            str(project_path),
            "--print",
            '{"tool":"run_next_milestone","arguments":{}}',
        ]
    )
    next_run = json.loads(capfd.readouterr().out)
    assert next_code == 0
    assert next_run["result"]["state"] == "MILESTONE_RUN_QUEUED"
    assert next_run["result"]["run_id"] != run_id
    assert next_run["exit"]["status"] == "MilestoneRunQueued"
    assert _load_context(project_path).pending_action is None


def test_invalid_stage_output_becomes_failed_fact_block_and_owner_activation(
    initialize_git_project: Callable[[], Path],
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    project_path = initialize_git_project()
    target_commit_sha = _approve_current_plan(project_path, monkeypatch, capfd)
    _publish_milestones(
        project_path,
        capfd,
        stages=[
            {
                "key": "stage-fail",
                "objective": "FAIL_STAGE_CONTRACT without a delivery document.",
            }
        ],
    )
    started = _request_and_start_first_run(project_path, capfd)
    run_id = started["run_id"]
    assert isinstance(run_id, str)

    drive_code = debug_tool_cli.main(
        ["--cwd", str(project_path), "--print", "drive-delivery"]
    )
    driven = json.loads(capfd.readouterr().out)
    assert drive_code == 0
    assert driven["result"]["outcome"] == "stage_failed"
    assert driven["result"]["context_status"] == "BLOCKED"
    assert driven["result"]["stage_run"]["status"] == "FAILED"
    assert "required delivery document" in driven["result"]["stage_run"]["failure"]
    assert driven["activation"]["task_type"] == "EXECUTION_RESULT"
    assert driven["activation"]["status"] == "PENDING"
    assert _git(project_path, "rev-parse", "HEAD") == target_commit_sha

    view_code = debug_tool_cli.main(
        ["--cwd", str(project_path), "--print", "view"]
    )
    view = json.loads(capfd.readouterr().out)["view"]
    assert view_code == 0
    assert view["context"]["status"] == "BLOCKED"
    assert view["context"]["current_run_id"] == run_id
    assert view["context"]["current_stage_key"] == "stage-fail"
    assert view["context"]["current_candidate_commit_sha"] is None
    assert view["stage_runs"][0]["status"] == "FAILED"
    assert view["allowed_actions"] == ["drive"]

    event_types = [event.event_type.value for event in _loaded_events(project_path)]
    assert "AGENT_INVOCATION_FAILED" in event_types
    assert "STAGE_RUN_FAILED" in event_types
    assert "CANDIDATE_READY" not in event_types
    candidate_ref = subprocess.run(
        [
            "git",
            "-C",
            str(project_path),
            "rev-parse",
            "--verify",
            f"refs/agentplanex/candidates/{run_id}",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert candidate_ref.returncode != 0


def test_request_then_approve_commits_specs_and_queues_owner_activation(
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
    monkeypatch.setattr(project_owner_service, "JBBModel", _ReplyingModel)
    approve_result = debug_tool_cli.main(
        ["--cwd", str(project_path), "--print", "approve"]
    )
    approve_response = json.loads(capfd.readouterr().out)

    context = _load_context(project_path)
    assert approve_result == 0
    assert approve_response["action"] == "approve"
    assert approve_response["result"]["status"] == "TODO"
    assert approve_response["result"]["pending_action"] is None
    assert approve_response["activation"]["task_type"] == "PLAN_DECISION"
    assert approve_response["activation"]["status"] == "PENDING"
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
    assert [event.event_type.value for event in _loaded_events(project_path)] == [
        "AGENT_INVOCATION_STARTED",
        "AGENT_INVOCATION_COMPLETED",
        "RUNTIME_CONTEXT_UPDATED",
        "PLAN_APPROVAL_REQUESTED",
        "RUNTIME_CONTEXT_UPDATED",
        "PLAN_APPROVED",
    ]

    drive_result = debug_tool_cli.main(
        ["--cwd", str(project_path), "--print", "drive"]
    )
    drive_response = json.loads(capfd.readouterr().out)

    assert drive_result == 0
    assert drive_response["result"]["status"] == "ReplyToHuman"
    assert drive_response["activation"]["status"] == "COMPLETED"
    activations = _loaded_activations(project_path)
    assert len(activations) == 1
    assert activations[0].status is OwnerActivationStatus.COMPLETED
    histories = _loaded_message_histories(project_path)
    events = _loaded_events(project_path)
    assert [event.event_type.value for event in events] == [
        "AGENT_INVOCATION_STARTED",
        "AGENT_INVOCATION_COMPLETED",
        "RUNTIME_CONTEXT_UPDATED",
        "PLAN_APPROVAL_REQUESTED",
        "RUNTIME_CONTEXT_UPDATED",
        "PLAN_APPROVED",
        "REACT_LOOP_ENTERED",
        "REACT_LOOP_EXITED",
    ]
    assert all(event.react_loop_id is None for event in events[:6])
    assert all(event.message_id is None for event in events[:4])
    assert events[4].message_id == histories[0].message_id
    assert events[5].message_id == histories[0].message_id
    assert events[5].payload == {
        "plan_commit_sha": context.current_plan_commit_sha
    }
    assert events[6].message_id == histories[0].message_id
    assert events[6].react_loop_id is not None
    assert events[7].react_loop_id == events[6].react_loop_id
    assert events[7].message_id == histories[1].message_id


def test_request_then_reject_does_not_commit_and_queues_owner_activation(
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

    monkeypatch.setattr(project_owner_service, "JBBModel", _ReplyingModel)
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
    assert reject_response["result"]["status"] == "TODO"
    assert reject_response["activation"]["task_type"] == "PLAN_DECISION"
    assert reject_response["activation"]["status"] == "PENDING"
    assert context.status == "TODO"
    assert context.pending_action is None
    assert context.current_plan_commit_sha is None
    assert _git(project_path, "rev-parse", "HEAD") == initial_head
    assert (
        "The user rejected the current Plan. "
        "Feedback: requirements are incomplete"
    ) in _loaded_message_contents(project_path)

    assert [event.event_type.value for event in _loaded_events(project_path)] == [
        "AGENT_INVOCATION_STARTED",
        "AGENT_INVOCATION_COMPLETED",
        "RUNTIME_CONTEXT_UPDATED",
        "PLAN_APPROVAL_REQUESTED",
        "RUNTIME_CONTEXT_UPDATED",
        "PLAN_REJECTED",
    ]
    drive_result = debug_tool_cli.main(
        ["--cwd", str(project_path), "--print", "drive"]
    )
    drive_response = json.loads(capfd.readouterr().out)
    assert drive_result == 0
    assert drive_response["result"]["status"] == "ReplyToHuman"
    assert drive_response["activation"]["status"] == "COMPLETED"
    histories = _loaded_message_histories(project_path)
    events = _loaded_events(project_path)
    assert [event.event_type.value for event in events] == [
        "AGENT_INVOCATION_STARTED",
        "AGENT_INVOCATION_COMPLETED",
        "RUNTIME_CONTEXT_UPDATED",
        "PLAN_APPROVAL_REQUESTED",
        "RUNTIME_CONTEXT_UPDATED",
        "PLAN_REJECTED",
        "REACT_LOOP_ENTERED",
        "REACT_LOOP_EXITED",
    ]
    assert events[4].message_id == histories[0].message_id
    assert events[5].message_id == histories[0].message_id
    assert events[5].payload == {}


def test_plain_text_submits_then_drives_a_restart_safe_user_activation(
    initialize_git_project: Callable[[], Path],
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    project_path = initialize_git_project()
    _ReplyingModel.queries = []
    monkeypatch.setattr(project_owner_service, "JBBModel", _ReplyingModel)

    result = debug_tool_cli.main(
        ["--cwd", str(project_path), "--print", "please inspect the plan"]
    )
    response = json.loads(capfd.readouterr().out)

    assert result == 0
    assert response["action"] == "message"
    assert response["activation"]["task_type"] == "USER_INPUT"
    assert response["activation"]["status"] == "PENDING"
    assert _ReplyingModel.queries == []
    assert _load_context(project_path).status == "TODO"
    assert _loaded_message_contents(project_path)[-1] == "please inspect the plan"

    first_drive = debug_tool_cli.main(
        ["--cwd", str(project_path), "--print", "drive"]
    )
    first_drive_response = json.loads(capfd.readouterr().out)
    assert first_drive == 0
    assert first_drive_response["result"] == {
        "status": "ReplyToHuman",
        "content": "please inspect the plan",
    }
    assert first_drive_response["activation"]["status"] == "COMPLETED"

    second_result = debug_tool_cli.main(
        ["--cwd", str(project_path), "--print", "continue"]
    )
    second_response = json.loads(capfd.readouterr().out)

    assert second_result == 0
    assert second_response["activation"]["status"] == "PENDING"
    second_drive = debug_tool_cli.main(
        ["--cwd", str(project_path), "--print", "drive"]
    )
    capfd.readouterr()
    assert second_drive == 0
    restored_contents = [
        message.get("content") for message in _ReplyingModel.queries[-1]
    ]
    assert restored_contents[-3:] == [
        "please inspect the plan",
        "please inspect the plan",
        "continue",
    ]
    assert all(
        activation.status is OwnerActivationStatus.COMPLETED
        for activation in _loaded_activations(project_path)
    )
