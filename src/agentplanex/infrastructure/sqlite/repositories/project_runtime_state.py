"""SQLite operations for persisted Project Runtime State."""

import sqlite3
from datetime import datetime
from typing import cast

from agentplanex.domains.project_runtime_state import ProjectRuntimeState


class SQLiteProjectRuntimeStateRepository:
    """Insert, update, and query the sole Feature Runtime State."""

    def insert(
        self,
        connection: sqlite3.Connection,
        context: ProjectRuntimeState,
    ) -> None:
        connection.execute(
            """
            INSERT INTO project_runtime_state (
                triage_id,
                idea,
                status,
                pending_action,
                git_branch,
                git_main_version,
                rolling_started_at,
                current_plan_commit_sha,
                pending_plan_subject_digest,
                current_snapshot_id,
                current_run_id,
                current_milestone_key,
                current_stage_key,
                current_candidate_commit_sha,
                blocked_reason,
                blocked_capability,
                blocked_previous_status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            self._values(context),
        )

    def update(
        self,
        connection: sqlite3.Connection,
        context: ProjectRuntimeState,
    ) -> None:
        cursor = connection.execute(
            """
            UPDATE project_runtime_state
            SET
                idea = ?,
                status = ?,
                pending_action = ?,
                git_branch = ?,
                git_main_version = ?,
                rolling_started_at = ?,
                current_plan_commit_sha = ?,
                pending_plan_subject_digest = ?,
                current_snapshot_id = ?,
                current_run_id = ?,
                current_milestone_key = ?,
                current_stage_key = ?,
                current_candidate_commit_sha = ?,
                blocked_reason = ?,
                blocked_capability = ?,
                blocked_previous_status = ?
            WHERE triage_id = ?
            """,
            (*self._values(context)[1:], context.triage_id),
        )
        if cursor.rowcount != 1:
            raise LookupError(f"Project runtime context not found: {context.triage_id}")

    def get(
        self,
        connection: sqlite3.Connection,
        triage_id: str,
    ) -> ProjectRuntimeState | None:
        row = connection.execute(
            f"{self._SELECT} WHERE triage_id = ?",
            (triage_id,),
        ).fetchone()
        if row is None:
            return None
        return self._from_row(row)

    def list_all(
        self,
        connection: sqlite3.Connection,
    ) -> tuple[ProjectRuntimeState, ...]:
        rows = connection.execute(
            f"{self._SELECT} ORDER BY triage_id"
        ).fetchall()
        return tuple(self._from_row(row) for row in rows)

    _COLUMNS = """
        triage_id,
        idea,
        status,
        pending_action,
        git_branch,
        git_main_version,
        rolling_started_at,
        current_plan_commit_sha,
        pending_plan_subject_digest,
        current_snapshot_id,
        current_run_id,
        current_milestone_key,
        current_stage_key,
        current_candidate_commit_sha,
        blocked_reason,
        blocked_capability,
        blocked_previous_status
    """
    _SELECT = f"SELECT {_COLUMNS} FROM project_runtime_state"

    @staticmethod
    def _values(context: ProjectRuntimeState) -> tuple[object, ...]:
        return (
            context.triage_id,
            context.idea,
            context.status,
            context.pending_action,
            context.git_branch,
            context.git_main_version,
            (
                context.rolling_started_at.isoformat()
                if context.rolling_started_at is not None
                else None
            ),
            context.current_plan_commit_sha,
            context.pending_plan_subject_digest,
            context.current_snapshot_id,
            context.current_run_id,
            context.current_milestone_key,
            context.current_stage_key,
            context.current_candidate_commit_sha,
            context.blocked_reason,
            context.blocked_capability,
            context.blocked_previous_status,
        )

    @staticmethod
    def _from_row(row: sqlite3.Row) -> ProjectRuntimeState:
        rolling_started_at = cast(str | None, row["rolling_started_at"])
        return ProjectRuntimeState(
            triage_id=cast(str, row["triage_id"]),
            idea=cast(str | None, row["idea"]),
            status=cast(str, row["status"]),
            pending_action=cast(str | None, row["pending_action"]),
            git_branch=cast(str | None, row["git_branch"]),
            git_main_version=cast(str | None, row["git_main_version"]),
            rolling_started_at=(
                datetime.fromisoformat(rolling_started_at)
                if rolling_started_at is not None
                else None
            ),
            current_plan_commit_sha=cast(
                str | None,
                row["current_plan_commit_sha"],
            ),
            pending_plan_subject_digest=cast(
                str | None,
                row["pending_plan_subject_digest"],
            ),
            current_snapshot_id=cast(str | None, row["current_snapshot_id"]),
            current_run_id=cast(str | None, row["current_run_id"]),
            current_milestone_key=cast(
                str | None,
                row["current_milestone_key"],
            ),
            current_stage_key=cast(str | None, row["current_stage_key"]),
            current_candidate_commit_sha=cast(
                str | None,
                row["current_candidate_commit_sha"],
            ),
            blocked_reason=cast(str | None, row["blocked_reason"]),
            blocked_capability=cast(str | None, row["blocked_capability"]),
            blocked_previous_status=cast(
                str | None,
                row["blocked_previous_status"],
            ),
        )
