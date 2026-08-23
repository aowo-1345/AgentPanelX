"""Public behavior for the single-Feature Runtime Context spine."""

import sqlite3
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields
from pathlib import Path
from threading import Event

import pytest

from agentplanex.bootstrap import (
    create_project_control_query,
    create_project_runtime,
    create_project_runtime_control,
)
from agentplanex.domains.project_runtime_state import ProjectRuntimeState
from agentplanex.infrastructure.sqlite import SQLiteDatabase
from agentplanex.infrastructure.sqlite.repositories import (
    SQLiteExecutionEventRepository,
    SQLiteProjectOwnerAgentRepository,
    SQLiteProjectRuntimeStateRepository,
)
from agentplanex.project_owner_agent.models.responses import ResponsesRequest
from agentplanex.project_runtime import ProjectRuntime
from agentplanex.project_runtime.errors import FeatureBusyError
from agentplanex.settings import DEFAULT_SETTINGS_PATH, load_settings


class _UnusedResponsesTransport:
    def create(self, _request: ResponsesRequest) -> object:
        raise AssertionError("Context-spine behavior must not call the model gateway")


class _BlockingResponsesTransport:
    def __init__(self) -> None:
        self.entered = Event()
        self.release = Event()

    def create(self, _request: ResponsesRequest) -> object:
        self.entered.set()
        if not self.release.wait(timeout=5):
            raise TimeoutError("test did not release the Owner model")
        return {
            "object": "response",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "done"}],
                }
            ],
        }


def _runtime(project_path: Path) -> ProjectRuntime:
    return create_project_runtime(
        project_path=project_path,
        settings=load_settings(DEFAULT_SETTINGS_PATH),
        approval_mode="yolo",
        responses_transport=_UnusedResponsesTransport(),
    )


def test_normal_runtime_exposes_only_the_feature_command_facade(
    initialize_git_project: Callable[[], Path],
) -> None:
    runtime = _runtime(initialize_git_project())

    assert [field.name for field in fields(ProjectRuntime)] == ["_service"]
    assert {
        name
        for name, member in vars(ProjectRuntime).items()
        if not name.startswith("_") and callable(member)
    } == {
        "initialize",
        "state",
        "begin_feature",
        "submit_message",
        "approve_plan",
        "reject_plan",
            "start_first_run",
            "approve_blocked_run",
            "reject_blocked_run",
            "drive_until_waiting",
        "fail_interrupted_work",
    }
    assert not any(
        hasattr(runtime, name)
        for name in (
            "context",
            "git",
            "executions",
            "project_control_view",
            "project_workspace_view",
            "drive_owner_model",
            "drive_delivery",
            "execute_tool",
        )
    )


def test_initialize_atomically_creates_and_restores_one_feature_identity(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()

    first = _runtime(project_path).initialize()
    restored = _runtime(project_path).initialize()

    assert isinstance(first, ProjectRuntimeState)
    assert restored == first
    assert not hasattr(first, "project_owner_agent")

    database = SQLiteDatabase.for_project(project_path)
    with database.read_only_connection() as connection:
        assert SQLiteProjectRuntimeStateRepository().list_all(connection) == (first,)
        owner = SQLiteProjectOwnerAgentRepository().get_by_triage_id(
            connection,
            first.triage_id,
        )
    assert owner is not None


def test_begin_feature_uses_initialized_identity_and_publishes_after_commit(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    runtime = _runtime(project_path)
    initialized = runtime.initialize()

    begun = runtime.begin_feature()

    assert begun.triage_id == initialized.triage_id
    assert begun.status == "TODO"
    database = SQLiteDatabase.for_project(project_path)
    with database.read_only_connection() as connection:
        persisted = SQLiteProjectRuntimeStateRepository().get(
            connection,
            initialized.triage_id,
        )
        events = SQLiteExecutionEventRepository().list_by_triage_id(
            connection,
            initialized.triage_id,
        )
    assert persisted == begun
    assert [event.payload["reason"] for event in events] == ["FEATURE_BEGUN"]


def test_non_initialization_command_does_not_create_feature(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    runtime = _runtime(project_path)

    with pytest.raises(LookupError, match="not initialized"):
        runtime.begin_feature()

    database = SQLiteDatabase.for_project(project_path)
    with database.read_only_connection() as connection:
        assert SQLiteProjectRuntimeStateRepository().list_all(connection) == ()
        assert connection.execute("SELECT COUNT(*) FROM project_owner_agent").fetchone()[0] == 0


def test_initialization_rolls_back_state_when_owner_identity_cannot_commit(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    runtime = _runtime(project_path)
    database = SQLiteDatabase.for_project(project_path)
    with database.transaction() as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_owner_identity
            BEFORE INSERT ON project_owner_agent
            BEGIN
                SELECT RAISE(ABORT, 'forced owner rollback');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced owner rollback"):
        runtime.initialize()

    with database.transaction() as connection:
        assert SQLiteProjectRuntimeStateRepository().list_all(connection) == ()
        assert connection.execute("SELECT COUNT(*) FROM project_owner_agent").fetchone()[0] == 0
        connection.execute("DROP TRIGGER reject_owner_identity")
    assert runtime.initialize().status == "TRIAGE"


def test_failed_transition_discards_state_cache_and_timeline_event(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    runtime = _runtime(project_path)
    initialized = runtime.initialize()
    database = SQLiteDatabase.for_project(project_path)
    with database.transaction() as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_feature_begin
            BEFORE UPDATE ON project_runtime_state
            BEGIN
                SELECT RAISE(ABORT, 'forced transition rollback');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced transition rollback"):
        runtime.begin_feature()

    with database.transaction() as connection:
        persisted = SQLiteProjectRuntimeStateRepository().get(
            connection,
            initialized.triage_id,
        )
        events = SQLiteExecutionEventRepository().list_by_triage_id(
            connection,
            initialized.triage_id,
        )
        connection.execute("DROP TRIGGER reject_feature_begin")
    assert persisted is not None
    assert persisted.status == "TRIAGE"
    assert events == ()
    assert runtime.begin_feature().status == "TODO"


def test_second_runtime_fails_fast_while_feature_operation_is_running(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    blocking_transport = _BlockingResponsesTransport()
    first = create_project_runtime(
        project_path=project_path,
        settings=load_settings(DEFAULT_SETTINGS_PATH),
        approval_mode="yolo",
        responses_transport=blocking_transport,
    )
    first.initialize()
    first.begin_feature()
    first.submit_message("hold the Feature operation")
    second = _runtime(project_path)
    control = create_project_runtime_control(
        project_path=project_path,
        settings=load_settings(DEFAULT_SETTINGS_PATH),
        approval_mode="yolo",
        responses_transport=_UnusedResponsesTransport(),
    )
    query = create_project_control_query(project_path=project_path)

    with ThreadPoolExecutor(max_workers=1) as pool:
        driving = pool.submit(first.drive_until_waiting)
        assert blocking_transport.entered.wait(timeout=5)
        assert query.get_current().state.status == "TODO"
        with pytest.raises(FeatureBusyError):
            second.initialize()
        with pytest.raises(FeatureBusyError):
            control.submit_message("This write must not race the Runtime.")
        blocking_transport.release.set()
        assert driving.result(timeout=5).status == "TODO"
