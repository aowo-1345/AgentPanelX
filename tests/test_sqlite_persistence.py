"""Observable SQLite persistence behavior."""

import shutil
import sqlite3
import subprocess
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agentplanex.domains import (
    MessageHistory,
    Milestone,
    MilestoneSnapshot,
    MilestoneState,
    OwnerActivation,
    OwnerActivationMode,
    OwnerActivationStatus,
    ProjectOwnerAgent,
    ProjectOwnerTaskType,
    ProjectRuntimeContext,
    Stage,
    StageRun,
    StageRunStatus,
    SummaryHistory,
)
from agentplanex.infrastructure.sqlite import SQLiteDatabase, initialize_schema
from agentplanex.infrastructure.sqlite.repositories import (
    SQLiteMessageHistoryRepository,
    SQLiteMilestoneSnapshotRepository,
    SQLiteOwnerActivationRepository,
    SQLiteProjectOwnerAgentRepository,
    SQLiteProjectRuntimeContextRepository,
    SQLiteStageRunRepository,
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
        pending_plan_subject_digest="reviewed-plan-digest",
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


def test_message_checkpoint_range_is_bounded_and_session_safe(
    project_path: Path,
) -> None:
    database = SQLiteDatabase.for_project(project_path)
    messages = SQLiteMessageHistoryRepository()
    initialize_schema(database)
    histories = (
        MessageHistory(
            "session-1",
            "message-1",
            1,
            ({"role": "user", "content": "covered"},),
        ),
        MessageHistory(
            "session-1",
            "message-2",
            2,
            ({"role": "assistant", "content": "tail"},),
        ),
        MessageHistory(
            "session-1",
            "message-3",
            3,
            ({"role": "user", "content": "trigger"},),
        ),
        MessageHistory(
            "session-2",
            "message-other-session",
            1,
            ({"role": "user", "content": "unrelated"},),
        ),
    )
    with database.transaction() as connection:
        for history in histories:
            messages.insert(connection, history)

    with database.connection() as connection:
        selected = messages.list_between_checkpoints(
            connection,
            "session-1",
            after_message_id="message-1",
            through_message_id="message-3",
        )
        with pytest.raises(ValueError, match="does not belong to Owner session"):
            messages.list_between_checkpoints(
                connection,
                "session-1",
                after_message_id="message-other-session",
                through_message_id="message-3",
            )
        with pytest.raises(ValueError, match="must precede activation message"):
            messages.list_between_checkpoints(
                connection,
                "session-1",
                after_message_id="message-3",
                through_message_id="message-3",
            )

    assert selected == histories[1:3]


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


def test_read_only_connection_rejects_runtime_writes(project_path: Path) -> None:
    database = SQLiteDatabase.for_project(project_path)
    initialize_schema(database)
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO project_runtime_context (triage_id, status)
            VALUES (?, ?)
            """,
            ("triage-read-only", "TODO"),
        )

    with database.read_only_connection() as connection:
        row = connection.execute(
            "SELECT status FROM project_runtime_context WHERE triage_id = ?",
            ("triage-read-only",),
        ).fetchone()
        assert row is not None
        assert row["status"] == "TODO"
        with pytest.raises(sqlite3.OperationalError):
            connection.execute(
                "UPDATE project_runtime_context SET status = ? WHERE triage_id = ?",
                ("DONE", "triage-read-only"),
            )


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
    assert schema_version[0] == 9

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
            "pending_plan_subject_digest",
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
            "created_at",
            "started_at",
            "lease_expires_at",
            "finished_at",
        ),
        "owner_activation": (
            "activation_id",
            "triage_id",
            "task_type",
            "message_id",
            "summary_id",
            "status",
            "driver_mode",
            "created_at",
            "started_at",
            "finished_at",
            "failure",
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


def test_schema_upgrades_v8_activation_checkpoints_without_losing_rows(
    project_path: Path,
) -> None:
    database = SQLiteDatabase.for_project(project_path)
    with database.transaction() as connection:
        connection.execute(
            """
            CREATE TABLE owner_activation (
                activation_id TEXT PRIMARY KEY,
                triage_id TEXT NOT NULL,
                task_type TEXT NOT NULL,
                message_id TEXT NOT NULL,
                status TEXT NOT NULL,
                driver_mode TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                failure TEXT
            )
            """
        )
        connection.execute(
            """
            INSERT INTO owner_activation (
                activation_id, triage_id, task_type, message_id, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "activation-existing",
                "triage-existing",
                "USER_INPUT",
                "message-existing",
                "PENDING",
                datetime(2026, 8, 5, tzinfo=UTC).isoformat(),
            ),
        )
        connection.execute("PRAGMA user_version = 8")

    initialize_schema(database)

    with database.connection() as connection:
        schema_version = connection.execute("PRAGMA user_version").fetchone()
        row = connection.execute(
            """
            SELECT activation_id, message_id, summary_id
            FROM owner_activation
            WHERE activation_id = ?
            """,
            ("activation-existing",),
        ).fetchone()
    assert schema_version is not None
    assert schema_version[0] == 9
    assert row is not None
    assert tuple(row) == ("activation-existing", "message-existing", None)


def test_competing_connections_claim_only_one_activation(
    project_path: Path,
) -> None:
    database = SQLiteDatabase.for_project(project_path)
    activations = SQLiteOwnerActivationRepository()
    initialize_schema(database)
    with database.transaction() as connection:
        for index in range(2):
            activations.insert(
                connection,
                OwnerActivation(
                    activation_id=f"activation-{index}",
                    triage_id="triage-claim",
                    task_type=ProjectOwnerTaskType.USER_INPUT,
                    message_id=f"message-{index}",
                ),
            )

    def claim():
        with database.transaction() as connection:
                return activations.claim_next(
                    connection,
                    "triage-claim",
                    datetime.now(UTC),
                    OwnerActivationMode.MODEL,
                )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = tuple(executor.submit(claim) for _ in range(2))
        results = tuple(future.result() for future in futures)

    assert sum(result is not None for result in results) == 1
    with database.connection() as connection:
        persisted = activations.list_by_triage_id(connection, "triage-claim")
    assert [activation.status for activation in persisted].count(
        OwnerActivationStatus.RUNNING
    ) == 1
    assert [activation.driver_mode for activation in persisted].count(
        OwnerActivationMode.MODEL
    ) == 1
    assert [activation.status for activation in persisted].count(
        OwnerActivationStatus.PENDING
    ) == 1


def test_milestone_snapshot_round_trips_complete_ordered_view(
    project_path: Path,
) -> None:
    database = SQLiteDatabase.for_project(project_path)
    snapshots = SQLiteMilestoneSnapshotRepository()
    initialize_schema(database)
    snapshot = MilestoneSnapshot(
        snapshot_id="snapshot-1",
        triage_id="triage-delivery",
        previous_snapshot_id=None,
        plan_commit_sha="plan-sha",
        milestones=(
            Milestone(
                key="M1",
                objective="Ship the first behavior.",
                state=MilestoneState.PENDING,
                stages=(
                    Stage(key="S1", objective="Implement the behavior."),
                    Stage(key="S2", objective="Verify the behavior."),
                ),
            ),
        ),
        reason="Initial delivery view.",
        message_id="message-1",
        created_at=datetime(2026, 8, 4, 12, tzinfo=UTC),
    )

    with database.transaction() as connection:
        snapshots.insert(connection, snapshot)
    with database.connection() as connection:
        loaded = snapshots.get(connection, snapshot.snapshot_id)

    assert loaded == snapshot
    assert loaded is not None
    assert loaded.first_pending() == snapshot.milestones[0]


def test_stage_run_claim_and_terminal_state_are_persisted(
    project_path: Path,
) -> None:
    database = SQLiteDatabase.for_project(project_path)
    stage_runs = SQLiteStageRunRepository()
    initialize_schema(database)
    created_at = datetime(2026, 8, 4, 12, tzinfo=UTC)
    queued = StageRun(
        stage_run_id="stage-run-1",
        triage_id="triage-delivery",
        run_id="run-1",
        snapshot_id="snapshot-1",
        milestone_key="M1",
        stage_key="S1",
        status=StageRunStatus.QUEUED,
        input_commit_sha="input-sha",
        output_commit_sha=None,
        failure=None,
        created_at=created_at,
    )

    with database.transaction() as connection:
        stage_runs.insert(connection, queued)
    with database.transaction() as connection:
        running = stage_runs.claim_next(
            connection,
            queued.triage_id,
            started_at=created_at + timedelta(seconds=1),
            lease_expires_at=created_at + timedelta(minutes=5),
        )
    assert running is not None
    assert running.status is StageRunStatus.RUNNING
    with database.transaction() as connection:
        succeeded = stage_runs.mark_succeeded(
            connection,
            queued.stage_run_id,
            output_commit_sha="output-sha",
            finished_at=created_at + timedelta(seconds=2),
        )

    assert succeeded.status is StageRunStatus.SUCCEEDED
    assert succeeded.output_commit_sha == "output-sha"
    with database.connection() as connection:
        assert stage_runs.get(connection, queued.stage_run_id) == succeeded
