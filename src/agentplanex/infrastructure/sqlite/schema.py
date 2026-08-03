"""SQLite schema initialization."""

from agentplanex.infrastructure.sqlite.database import SQLiteDatabase

SCHEMA_VERSION = 3

_INITIAL_SCHEMA = (
    """
    CREATE TABLE project_runtime_context (
        triage_id TEXT PRIMARY KEY,
        idea TEXT,
        status TEXT NOT NULL DEFAULT 'TRIAGE'
            CHECK (status IN (
                'TRIAGE', 'TODO', 'READY', 'IN_PROGRESS', 'BLOCKED', 'DONE'
            )),
        pending_action TEXT
            CHECK (pending_action IN ('PLAN_APPROVAL', 'FIRST_RUN_APPROVAL')),
        git_branch TEXT,
        git_main_version TEXT,
        rolling_started_at TEXT,
        current_plan_commit_sha TEXT,
        current_snapshot_id TEXT,
        current_run_id TEXT,
        current_milestone_key TEXT,
        current_stage_key TEXT,
        current_candidate_commit_sha TEXT
    )
    """,
    """
    CREATE TABLE project_owner_agent (
        triage_id TEXT NOT NULL UNIQUE,
        project_owner_session_id TEXT PRIMARY KEY,
        system_prompt TEXT NOT NULL,
        tools TEXT NOT NULL,
        summary_id TEXT,
        message_id TEXT
    )
    """,
    """
    CREATE TABLE summary_history (
        project_owner_session_id TEXT NOT NULL,
        summary_id TEXT PRIMARY KEY,
        covered_through_message_id TEXT,
        summary_content TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX summary_history_session_id_idx
    ON summary_history (project_owner_session_id)
    """,
    """
    CREATE TABLE message_history (
        project_owner_session_id TEXT NOT NULL,
        message_id TEXT PRIMARY KEY,
        sequence INTEGER NOT NULL,
        message TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX message_history_session_id_idx
    ON message_history (project_owner_session_id)
    """,
    """
    CREATE UNIQUE INDEX message_history_session_sequence_idx
    ON message_history (project_owner_session_id, sequence)
    """,
    """
    CREATE TABLE milestone_snapshot (
        snapshot_id TEXT PRIMARY KEY,
        triage_id TEXT NOT NULL,
        previous_snapshot_id TEXT,
        plan_commit_sha TEXT NOT NULL,
        milestones TEXT NOT NULL,
        reason TEXT NOT NULL,
        message_id TEXT,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX milestone_snapshot_triage_created_idx
    ON milestone_snapshot (triage_id, created_at)
    """,
    """
    CREATE TABLE stage_run (
        stage_run_id TEXT PRIMARY KEY,
        triage_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        snapshot_id TEXT NOT NULL,
        milestone_key TEXT NOT NULL,
        stage_key TEXT NOT NULL,
        status TEXT NOT NULL,
        input_commit_sha TEXT NOT NULL,
        output_commit_sha TEXT,
        failure TEXT,
        started_at TEXT NOT NULL,
        finished_at TEXT
    )
    """,
    """
    CREATE INDEX stage_run_triage_run_idx
    ON stage_run (triage_id, run_id)
    """,
    """
    CREATE INDEX stage_run_snapshot_milestone_idx
    ON stage_run (snapshot_id, milestone_key, stage_key)
    """,
    """
    CREATE TABLE execution_event (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
        triage_id TEXT NOT NULL,
        event_type TEXT NOT NULL,
        activation_id TEXT,
        message_id TEXT,
        payload TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX execution_event_triage_event_idx
    ON execution_event (triage_id, event_id)
    """,
    """
    CREATE INDEX execution_event_message_id_idx
    ON execution_event (message_id)
    """,
)


def initialize_schema(database: SQLiteDatabase) -> None:
    """Create the initial schema or verify its supported version."""
    with database.transaction() as connection:
        row = connection.execute("PRAGMA user_version").fetchone()
        current_version = int(row[0]) if row is not None else 0

        if current_version > SCHEMA_VERSION:
            raise RuntimeError(
                "SQLite schema version "
                f"{current_version} is newer than supported version {SCHEMA_VERSION}"
            )
        if current_version == 0:
            for statement in _INITIAL_SCHEMA:
                connection.execute(statement)
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            return

        if current_version != SCHEMA_VERSION:
            raise RuntimeError(
                f"Unsupported SQLite schema version: {current_version}; "
                "recreate this development database"
            )
