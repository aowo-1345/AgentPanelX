"""SQLite schema initialization."""

import sqlite3

from agentplanex.infrastructure.sqlite.database import SQLiteDatabase

SCHEMA_VERSION = 2

_INITIAL_SCHEMA = (
    """
    CREATE TABLE project_runtime_context (
        triage_id TEXT PRIMARY KEY,
        status TEXT,
        git_main_version TEXT
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

        if current_version == 1:
            _migrate_v1_to_v2(connection)
            connection.execute("PRAGMA user_version = 2")
            return

        if current_version != SCHEMA_VERSION:
            raise RuntimeError(f"Unsupported SQLite schema version: {current_version}")


def _migrate_v1_to_v2(connection: sqlite3.Connection) -> None:
    """Add deterministic append order to the original message-history table."""
    connection.execute("ALTER TABLE message_history ADD COLUMN sequence INTEGER")
    connection.execute(
        """
        UPDATE message_history AS current
        SET sequence = (
            SELECT COUNT(*)
            FROM message_history AS earlier
            WHERE earlier.project_owner_session_id = current.project_owner_session_id
              AND earlier.rowid <= current.rowid
        )
        """
    )
    connection.execute(
        """
        CREATE UNIQUE INDEX message_history_session_sequence_idx
        ON message_history (project_owner_session_id, sequence)
        """
    )
