"""SQLite schema initialization."""

from agentplanex.infrastructure.sqlite.database import SQLiteDatabase

SCHEMA_VERSION = 14

_INITIAL_SCHEMA = (
    """
    CREATE TABLE project_runtime_state (
        triage_id TEXT PRIMARY KEY,
        idea TEXT,
        status TEXT NOT NULL DEFAULT 'TRIAGE'
            CHECK (status IN (
                'TRIAGE', 'TODO', 'READY', 'IN_PROGRESS', 'BLOCKED', 'DONE'
            )),
        pending_action TEXT
            CHECK (pending_action IN (
                'PLAN_APPROVAL', 'FIRST_RUN_APPROVAL', 'BLOCKED_RUN_APPROVAL'
            )),
        git_branch TEXT,
        git_main_version TEXT,
        rolling_started_at TEXT,
        current_plan_commit_sha TEXT,
        pending_plan_subject_digest TEXT,
        current_snapshot_id TEXT,
        current_run_id TEXT,
        current_milestone_key TEXT,
        current_stage_key TEXT,
        current_candidate_commit_sha TEXT,
        blocked_reason TEXT,
        blocked_capability TEXT,
        blocked_previous_status TEXT
            CHECK (blocked_previous_status IN ('TODO', 'READY', 'IN_PROGRESS'))
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
        covered_through_message_id TEXT NOT NULL,
        intent_summary_content TEXT NOT NULL
            CHECK (length(trim(intent_summary_content)) > 0),
        trajectory_summary_content TEXT NOT NULL
            CHECK (length(trim(trajectory_summary_content)) > 0),
        UNIQUE (project_owner_session_id, covered_through_message_id)
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
        status TEXT NOT NULL
            CHECK (status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED')),
        input_commit_sha TEXT NOT NULL,
        output_commit_sha TEXT,
        failure TEXT,
        created_at TEXT NOT NULL,
        started_at TEXT,
        lease_expires_at TEXT,
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
    CREATE UNIQUE INDEX stage_run_one_active_idx
    ON stage_run (triage_id)
    WHERE status IN ('QUEUED', 'RUNNING')
    """,
    """
    CREATE TABLE owner_activation (
        activation_id TEXT PRIMARY KEY,
        triage_id TEXT NOT NULL,
        task_type TEXT NOT NULL
            CHECK (task_type IN ('USER_INPUT', 'PLAN_DECISION', 'EXECUTION_RESULT')),
        message_id TEXT NOT NULL,
        summary_id TEXT,
        status TEXT NOT NULL
            CHECK (status IN ('PENDING', 'RUNNING', 'COMPLETED', 'FAILED')),
        driver_mode TEXT
            CHECK (driver_mode IN ('MODEL', 'TOOL')),
        created_at TEXT NOT NULL,
        started_at TEXT,
        finished_at TEXT,
        failure TEXT,
        CHECK (
            (
                status = 'PENDING'
                AND (driver_mode IS NULL OR driver_mode = 'TOOL')
            )
            OR (status != 'PENDING' AND driver_mode IS NOT NULL)
        )
    )
    """,
    """
    CREATE INDEX owner_activation_triage_status_idx
    ON owner_activation (triage_id, status, created_at, activation_id)
    """,
    """
    CREATE UNIQUE INDEX owner_activation_one_unfinished_idx
    ON owner_activation (triage_id)
    WHERE status IN ('PENDING', 'RUNNING')
    """,
    """
    CREATE UNIQUE INDEX owner_activation_message_idx
    ON owner_activation (triage_id, message_id)
    """,
    """
    CREATE TABLE execution_event (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
        triage_id TEXT NOT NULL,
        event_type TEXT NOT NULL,
        react_loop_id TEXT,
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
    """
    CREATE TABLE auto_takeover_run (
        run_id TEXT PRIMARY KEY,
        triage_id TEXT NOT NULL,
        trigger_event_id INTEGER NOT NULL UNIQUE,
        status TEXT NOT NULL
            CHECK (status IN ('RUNNING', 'YES', 'NO', 'FAILED')),
        decision TEXT CHECK (decision IN ('YES', 'NO')),
        attribution TEXT,
        error TEXT,
        started_at TEXT NOT NULL,
        finished_at TEXT
    )
    """,
    """
    CREATE UNIQUE INDEX auto_takeover_one_active_feature_idx
    ON auto_takeover_run (triage_id)
    WHERE status = 'RUNNING'
    """,
    """
    CREATE TABLE auto_takeover_attempt (
        attempt_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        ordinal INTEGER NOT NULL CHECK (ordinal IN (1, 2)),
        status TEXT NOT NULL
            CHECK (status IN ('RUNNING', 'INVALID', 'COMPLETED', 'FAILED')),
        fence_token TEXT UNIQUE,
        error TEXT,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        FOREIGN KEY (run_id) REFERENCES auto_takeover_run(run_id),
        UNIQUE (run_id, ordinal)
    )
    """,
    """
    CREATE UNIQUE INDEX auto_takeover_one_active_attempt_idx
    ON auto_takeover_attempt (run_id)
    WHERE status = 'RUNNING'
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


def verify_schema(database: SQLiteDatabase) -> None:
    """Verify the current schema through a strictly read-only connection."""

    with database.read_only_connection() as connection:
        row = connection.execute("PRAGMA user_version").fetchone()
        current_version = int(row[0]) if row is not None else 0
    if current_version != SCHEMA_VERSION:
        raise RuntimeError(
            "Historical Owner Fork requires SQLite schema version "
            f"{SCHEMA_VERSION}, found {current_version}"
        )
