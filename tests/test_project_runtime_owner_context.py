"""Public Owner behavior routed through the Feature Runtime Context."""

import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest

import agentplanex.services as services
from agentplanex.infrastructure.sqlite import SQLiteDatabase
from agentplanex.infrastructure.sqlite.repositories import (
    SQLiteProjectOwnerAgentRepository,
    SQLiteProjectRuntimeStateRepository,
)
from agentplanex.project_owner_agent.models.responses import ResponsesRequest
from agentplanex.project_runtime import ProjectRuntime
from agentplanex.settings import DEFAULT_SETTINGS_PATH, load_settings


class _UnusedResponsesTransport:
    def create(self, _request: ResponsesRequest) -> object:
        raise AssertionError("Owner persistence tests must not call a model gateway")


def _runtime(project_path: Path) -> ProjectRuntime:
    return ProjectRuntime(
        project_path=project_path,
        settings=load_settings(DEFAULT_SETTINGS_PATH),
        approval_mode="yolo",
        responses_transport=_UnusedResponsesTransport(),
    )


def test_failed_activation_insert_leaves_no_orphan_owner_input(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    runtime = _runtime(project_path)
    initialized = runtime.initialize()
    database = SQLiteDatabase.for_project(project_path)
    with database.transaction() as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_owner_activation
            BEFORE INSERT ON owner_activation
            BEGIN
                SELECT RAISE(ABORT, 'forced activation rollback');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced activation rollback"):
        runtime.submit_message("this input must roll back")

    with database.read_only_connection() as connection:
        state = SQLiteProjectRuntimeStateRepository().get(
            connection,
            initialized.triage_id,
        )
        owner = SQLiteProjectOwnerAgentRepository().get_by_triage_id(
            connection,
            initialized.triage_id,
        )
        message_count = connection.execute("SELECT COUNT(*) FROM message_history").fetchone()[
            0
        ]
        activation_count = connection.execute(
            "SELECT COUNT(*) FROM owner_activation"
        ).fetchone()[0]
        event_count = connection.execute("SELECT COUNT(*) FROM execution_event").fetchone()[
            0
        ]
    assert state is not None
    assert state.status == "TRIAGE"
    assert owner is not None
    assert owner.message_id is None
    assert message_count == 0
    assert activation_count == 0
    assert event_count == 0


def test_services_package_does_not_export_legacy_project_owner_service() -> None:
    assert "ProjectOwnerService" not in services.__all__
    assert not hasattr(services, "ProjectOwnerService")
