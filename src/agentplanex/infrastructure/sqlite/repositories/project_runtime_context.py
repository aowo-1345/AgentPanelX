"""SQLite operations for project runtime contexts."""

import sqlite3
from typing import cast

from agentplanex.domains import ProjectRuntimeContext


class SQLiteProjectRuntimeContextRepository:
    """Insert, update, and query project runtime contexts."""

    def insert(
        self,
        connection: sqlite3.Connection,
        context: ProjectRuntimeContext,
    ) -> None:
        connection.execute(
            """
            INSERT INTO project_runtime_context (triage_id, status, git_main_version)
            VALUES (?, ?, ?)
            """,
            (context.triage_id, context.status, context.git_main_version),
        )

    def update(
        self,
        connection: sqlite3.Connection,
        context: ProjectRuntimeContext,
    ) -> None:
        cursor = connection.execute(
            """
            UPDATE project_runtime_context
            SET status = ?, git_main_version = ?
            WHERE triage_id = ?
            """,
            (context.status, context.git_main_version, context.triage_id),
        )
        if cursor.rowcount != 1:
            raise LookupError(f"Project runtime context not found: {context.triage_id}")

    def get(
        self,
        connection: sqlite3.Connection,
        triage_id: str,
    ) -> ProjectRuntimeContext | None:
        row = connection.execute(
            """
            SELECT triage_id, status, git_main_version
            FROM project_runtime_context
            WHERE triage_id = ?
            """,
            (triage_id,),
        ).fetchone()
        if row is None:
            return None
        return ProjectRuntimeContext(
            triage_id=cast(str, row["triage_id"]),
            status=cast(str | None, row["status"]),
            git_main_version=cast(str | None, row["git_main_version"]),
        )

    def list_all(
        self,
        connection: sqlite3.Connection,
    ) -> tuple[ProjectRuntimeContext, ...]:
        rows = connection.execute(
            """
            SELECT triage_id, status, git_main_version
            FROM project_runtime_context
            ORDER BY triage_id
            """
        ).fetchall()
        return tuple(
            ProjectRuntimeContext(
                triage_id=cast(str, row["triage_id"]),
                status=cast(str | None, row["status"]),
                git_main_version=cast(str | None, row["git_main_version"]),
            )
            for row in rows
        )
