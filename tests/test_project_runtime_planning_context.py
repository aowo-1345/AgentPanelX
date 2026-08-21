"""Plan lifecycle behavior through the shared Feature Runtime Context."""

import sqlite3
from collections.abc import Callable
from dataclasses import fields
from pathlib import Path

import pytest

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
