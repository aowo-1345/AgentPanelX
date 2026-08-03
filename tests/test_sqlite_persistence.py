"""Observable SQLite persistence behavior."""

import shutil
import subprocess
from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentplanex.domains import (
    MessageHistory,
    ProjectOwnerAgent,
    ProjectRuntimeContext,
    SummaryHistory,
)
from agentplanex.infrastructure.sqlite import SQLiteDatabase, initialize_schema
from agentplanex.infrastructure.sqlite.repositories import (
    SQLiteMessageHistoryRepository,
    SQLiteProjectOwnerAgentRepository,
    SQLiteProjectRuntimeContextRepository,
    SQLiteSummaryHistoryRepository,
)


@pytest.fixture
def project_path(request: pytest.FixtureRequest) -> Iterator[Path]:
    directory = (
        Path(__file__).resolve().parent.parent
        / ".agentplanex"
        / "tests"
        / request.node.name
    )
    shutil.rmtree(directory, ignore_errors=True)
    directory.mkdir(parents=True)
    yield directory
    shutil.rmtree(directory, ignore_errors=True)


def test_context_can_be_reloaded_and_assembled(project_path: Path) -> None:
    database = SQLiteDatabase.for_project(project_path)
    runtimes = SQLiteProjectRuntimeContextRepository()
    owners = SQLiteProjectOwnerAgentRepository()
    summaries = SQLiteSummaryHistoryRepository()
    messages = SQLiteMessageHistoryRepository()
    initialize_schema(database)

    summary = SummaryHistory(
        "session-1",
        "summary-1",
        "The project is being refactored.",
        covered_through_message_id="message-1",
    )
    message_history = MessageHistory(
        "session-1",
        "message-1",
        1,
        (
            {"role": "user", "content": "Continue the refactor."},
            {"role": "assistant", "content": "I will inspect the current state."},
        ),
    )
    owner = ProjectOwnerAgent(
        triage_id="triage-1",
        project_owner_session_id="session-1",
        system_prompt="Own the project.",
        tools=("bash",),
        summary_id=summary.summary_id,
        message_id=message_history.message_id,
    )
    runtime = ProjectRuntimeContext(
        triage_id="triage-1",
        idea="Ship the runtime control plane.",
        status="BLOCKED",
        pending_action="PLAN_APPROVAL",
        git_branch="feature/runtime-control",
        git_main_version="main-commit",
        rolling_started_at=datetime(2026, 8, 3, 12, 30, tzinfo=UTC),
        current_plan_commit_sha="plan-commit",
        current_snapshot_id="snapshot-2",
        current_run_id="run-4",
        current_milestone_key="milestone-2",
        current_stage_key="stage-1",
        current_candidate_commit_sha="candidate-commit",
    )

    with database.transaction() as connection:
        runtimes.insert(connection, runtime)
        summaries.insert(connection, summary)
        messages.insert(connection, message_history)
        owners.insert(connection, owner)

    initialize_schema(database)
    with database.connection() as connection:
        loaded_runtime = runtimes.get(connection, "triage-1")
        loaded_owner = owners.get_by_triage_id(connection, "triage-1")
        assert loaded_runtime is not None
        assert loaded_owner is not None
        assert loaded_owner.summary_id is not None
        assert loaded_owner.message_id is not None
        loaded_summary = summaries.get(connection, loaded_owner.summary_id)
        loaded_messages = messages.get(connection, loaded_owner.message_id)

    assembled_owner = replace(
        loaded_owner,
        summary_history=loaded_summary,
        message_history=loaded_messages,
    )
    assembled_runtime = replace(
        loaded_runtime,
        project_owner_agent=assembled_owner,
    )

    assert loaded_runtime == runtime
    assert assembled_runtime.project_owner_agent is not None
    assert assembled_runtime.project_owner_agent.summary_history == summary
    assert assembled_runtime.project_owner_agent.message_history == message_history


def test_failed_transaction_does_not_leave_partial_state(
    project_path: Path,
) -> None:
    database = SQLiteDatabase.for_project(project_path)
    runtimes = SQLiteProjectRuntimeContextRepository()
    initialize_schema(database)

    with (
        pytest.raises(RuntimeError, match="stop the transaction"),
        database.transaction() as connection,
    ):
        runtimes.insert(
            connection,
            ProjectRuntimeContext(
                triage_id="triage-rollback",
                status="TODO",
                git_main_version="main",
            ),
        )
        raise RuntimeError("stop the transaction")

    with database.connection() as connection:
        assert runtimes.get(connection, "triage-rollback") is None


def test_git_project_fixture_initializes_project_database(
    initialize_git_project: Callable[[], Path],
) -> None:
    fixture_project = initialize_git_project()
    database = SQLiteDatabase.for_project(fixture_project)

    assert database.path == fixture_project / ".agentplanex" / "agentplanex.sqlite3"
    assert database.path.is_file()
    with database.connection() as connection:
        schema_version = connection.execute("PRAGMA user_version").fetchone()
    assert schema_version is not None
    assert schema_version[0] == 4

    git_status = subprocess.run(
        ["git", "-C", str(fixture_project), "status", "--short"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert git_status.stdout == ""


def test_schema_contains_current_control_plane_tables_and_columns(
    project_path: Path,
) -> None:
    database = SQLiteDatabase.for_project(project_path)
    initialize_schema(database)

    expected_columns = {
        "project_runtime_context": (
            "triage_id",
            "idea",
            "status",
            "pending_action",
            "git_branch",
            "git_main_version",
            "rolling_started_at",
            "current_plan_commit_sha",
            "current_snapshot_id",
            "current_run_id",
            "current_milestone_key",
            "current_stage_key",
            "current_candidate_commit_sha",
        ),
        "project_owner_agent": (
            "triage_id",
            "project_owner_session_id",
            "system_prompt",
            "tools",
            "summary_id",
            "message_id",
        ),
        "message_history": (
            "project_owner_session_id",
            "message_id",
            "sequence",
            "message",
        ),
        "summary_history": (
            "project_owner_session_id",
            "summary_id",
            "covered_through_message_id",
            "summary_content",
        ),
        "milestone_snapshot": (
            "snapshot_id",
            "triage_id",
            "previous_snapshot_id",
            "plan_commit_sha",
            "milestones",
            "reason",
            "message_id",
            "created_at",
        ),
        "stage_run": (
            "stage_run_id",
            "triage_id",
            "run_id",
            "snapshot_id",
            "milestone_key",
            "stage_key",
            "status",
            "input_commit_sha",
            "output_commit_sha",
            "failure",
            "started_at",
            "finished_at",
        ),
        "execution_event": (
            "event_id",
            "triage_id",
            "event_type",
            "react_loop_id",
            "message_id",
            "payload",
            "created_at",
        ),
    }

    with database.connection() as connection:
        actual_tables = {
            row["name"]
            for row in connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                """
            ).fetchall()
        }
        assert actual_tables == set(expected_columns)
        for table, columns in expected_columns.items():
            actual_columns = tuple(
                row["name"]
                for row in connection.execute(
                    f"PRAGMA table_info({table})"
                ).fetchall()
            )
            assert actual_columns == columns
