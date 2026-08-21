"""Plan lifecycle behavior through the shared Feature Runtime Context."""

import sqlite3
from collections.abc import Callable
from dataclasses import fields
from pathlib import Path

import pytest

from agentplanex.infrastructure.git_repository import GitRepository
from agentplanex.infrastructure.sqlite import SQLiteDatabase
from agentplanex.infrastructure.sqlite.repositories import (
    SQLiteExecutionEventRepository,
    SQLiteOwnerActivationRepository,
    SQLiteProjectOwnerAgentRepository,
    SQLiteProjectRuntimeStateRepository,
)
from agentplanex.project_owner_agent.models.responses import ResponsesRequest
from agentplanex.project_runtime import ProjectRuntime
from agentplanex.services.planning import PlanningService
from agentplanex.settings import DEFAULT_SETTINGS_PATH, load_settings


class _UnusedResponsesTransport:
    def create(self, _request: ResponsesRequest) -> object:
        raise AssertionError("Plan lifecycle tests must not call a model gateway")


class _FailingResponsesTransport:
    def create(self, _request: ResponsesRequest) -> object:
        raise RuntimeError("deterministic Owner failure")


class _ReplyingResponsesTransport:
    def create(self, _request: ResponsesRequest) -> object:
        return {
            "object": "response",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Plan decision received."}],
                }
            ],
        }


def _runtime(project_path: Path) -> ProjectRuntime:
    return ProjectRuntime(
        project_path=project_path,
        settings=load_settings(DEFAULT_SETTINGS_PATH),
        approval_mode="yolo",
        responses_transport=_UnusedResponsesTransport(),
    )


def _write_specs(project_path: Path) -> None:
    for name in ("architecture.md", "requirements.md", "roadmap.md"):
        (project_path / name).write_text(f"# {name}\n", encoding="utf-8")


def test_rejected_plan_activation_failure_rolls_back_state_and_owner_message(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    runtime = _runtime(project_path)
    state = runtime.initialize()
    _write_specs(project_path)
    requested = runtime.execute_action(
        {
            "tool": "request_plan_approval",
            "call_id": "request-plan",
            "arguments": {},
        }
    )
    assert requested.exit is not None

    database = SQLiteDatabase.for_project(project_path)
    with database.transaction() as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_plan_activation
            BEFORE INSERT ON owner_activation
            BEGIN
                SELECT RAISE(ABORT, 'forced plan activation rollback');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced plan activation rollback"):
        runtime.reject_plan("keep the current plan pending")

    with database.read_only_connection() as connection:
        persisted = SQLiteProjectRuntimeStateRepository().get(
            connection,
            state.triage_id,
        )
        owner = SQLiteProjectOwnerAgentRepository().get_by_triage_id(
            connection,
            state.triage_id,
        )
        activations = SQLiteOwnerActivationRepository().list_by_triage_id(
            connection,
            state.triage_id,
        )
        events = SQLiteExecutionEventRepository().list_by_triage_id(
            connection,
            state.triage_id,
        )
    assert persisted is not None
    assert persisted.pending_action == "PLAN_APPROVAL"
    assert persisted.pending_plan_subject_digest is not None
    assert owner is not None
    assert owner.message_id is None
    assert activations == ()
    assert [event.event_type.value for event in events] == [
        "RUNTIME_CONTEXT_UPDATED",
        "PLAN_APPROVAL_REQUESTED",
    ]


def test_planning_has_one_context_state_path() -> None:
    dependency_names = {field.name for field in fields(PlanningService)}
    assert "context" in dependency_names
    assert dependency_names.isdisjoint({"database", "contexts", "runtime_contexts"})
    assert not hasattr(PlanningService, "for_project")


def test_approve_retry_reuses_unapproved_plan_checkpoint(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    runtime = _runtime(project_path)
    state = runtime.initialize()
    _write_specs(project_path)
    requested = runtime.execute_action(
        {
            "tool": "request_plan_approval",
            "call_id": "request-plan",
            "arguments": {},
        }
    )
    assert requested.exit is not None
    head_before = GitRepository(project_path).head_sha()
    database = SQLiteDatabase.for_project(project_path)
    with database.transaction() as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_approved_plan_activation
            BEFORE INSERT ON owner_activation
            BEGIN
                SELECT RAISE(ABORT, 'forced approve rollback');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced approve rollback"):
        runtime.approve_plan()

    checkpoint = GitRepository(project_path).head_sha()
    assert checkpoint != head_before
    with database.transaction() as connection:
        persisted = SQLiteProjectRuntimeStateRepository().get(
            connection,
            state.triage_id,
        )
        connection.execute("DROP TRIGGER reject_approved_plan_activation")
    assert persisted is not None
    assert persisted.pending_action == "PLAN_APPROVAL"
    assert persisted.current_plan_commit_sha is None

    approved = runtime.approve_plan()

    assert approved.commit_sha == checkpoint
    assert GitRepository(project_path).head_sha() == checkpoint
    assert approved.state.current_plan_commit_sha == checkpoint


def test_plan_decision_does_not_clear_an_unrelated_runtime_failure(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    failing = ProjectRuntime(
        project_path=project_path,
        settings=load_settings(DEFAULT_SETTINGS_PATH),
        approval_mode="yolo",
        responses_transport=_FailingResponsesTransport(),
    )
    failing.initialize()
    failing.begin_feature()
    failing.submit_message("Block this Runtime before replanning.")
    assert failing.drive_until_waiting().status == "BLOCKED"
    _write_specs(project_path)
    requested = failing.execute_action(
        {
            "tool": "request_plan_approval",
            "call_id": "request-blocked-plan",
            "arguments": {},
        }
    )
    assert requested.exit is not None

    resumed = ProjectRuntime(
        project_path=project_path,
        settings=load_settings(DEFAULT_SETTINGS_PATH),
        approval_mode="yolo",
        responses_transport=_ReplyingResponsesTransport(),
    )
    decision = resumed.approve_plan()

    assert decision.state.status == "BLOCKED"
    assert resumed.drive_until_waiting().status == "BLOCKED"
    driven = resumed.drive_next_activation()
    assert driven.activation is not None
    assert driven.activation.status.value == "COMPLETED"
    assert resumed.state().status == "BLOCKED"
    assert resumed.project_control_view().owner_activation is None
