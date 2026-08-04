"""SQLite persistence for durable Stage execution requests."""

import sqlite3
from datetime import datetime
from typing import cast

from agentplanex.domains.delivery import StageRun, StageRunStatus


class SQLiteStageRunRepository:
    """Persist and atomically claim the sole active StageRun for a Triage."""

    def insert(self, connection: sqlite3.Connection, stage_run: StageRun) -> None:
        connection.execute(
            """
            INSERT INTO stage_run (
                stage_run_id,
                triage_id,
                run_id,
                snapshot_id,
                milestone_key,
                stage_key,
                status,
                input_commit_sha,
                output_commit_sha,
                failure,
                created_at,
                started_at,
                lease_expires_at,
                finished_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            self._values(stage_run),
        )

    def get(
        self,
        connection: sqlite3.Connection,
        stage_run_id: str,
    ) -> StageRun | None:
        row = connection.execute(
            f"{self._SELECT} WHERE stage_run_id = ?",
            (stage_run_id,),
        ).fetchone()
        return self._from_row(row) if row is not None else None

    def get_active(
        self,
        connection: sqlite3.Connection,
        triage_id: str,
    ) -> StageRun | None:
        row = connection.execute(
            f"""
            {self._SELECT}
            WHERE triage_id = ? AND status IN (?, ?)
            ORDER BY created_at, stage_run_id
            LIMIT 1
            """,
            (triage_id, StageRunStatus.QUEUED.value, StageRunStatus.RUNNING.value),
        ).fetchone()
        return self._from_row(row) if row is not None else None

    def list_by_run_id(
        self,
        connection: sqlite3.Connection,
        run_id: str,
    ) -> tuple[StageRun, ...]:
        rows = connection.execute(
            f"{self._SELECT} WHERE run_id = ? ORDER BY created_at, stage_run_id",
            (run_id,),
        ).fetchall()
        return tuple(self._from_row(row) for row in rows)

    def list_by_triage_id(
        self,
        connection: sqlite3.Connection,
        triage_id: str,
    ) -> tuple[StageRun, ...]:
        rows = connection.execute(
            f"{self._SELECT} WHERE triage_id = ? ORDER BY created_at, stage_run_id",
            (triage_id,),
        ).fetchall()
        return tuple(self._from_row(row) for row in rows)

    def claim_next(
        self,
        connection: sqlite3.Connection,
        triage_id: str,
        *,
        started_at: datetime,
        lease_expires_at: datetime,
    ) -> StageRun | None:
        row = connection.execute(
            f"""
            UPDATE stage_run
            SET status = ?, started_at = ?, lease_expires_at = ?
            WHERE stage_run_id = (
                SELECT stage_run_id
                FROM stage_run
                WHERE triage_id = ? AND status = ?
                ORDER BY created_at, stage_run_id
                LIMIT 1
            ) AND status = ?
            RETURNING {self._COLUMNS}
            """,
            (
                StageRunStatus.RUNNING.value,
                _encode_datetime(started_at),
                _encode_datetime(lease_expires_at),
                triage_id,
                StageRunStatus.QUEUED.value,
                StageRunStatus.QUEUED.value,
            ),
        ).fetchone()
        return self._from_row(row) if row is not None else None

    def mark_succeeded(
        self,
        connection: sqlite3.Connection,
        stage_run_id: str,
        *,
        output_commit_sha: str,
        finished_at: datetime,
    ) -> StageRun:
        return self._finish(
            connection,
            stage_run_id=stage_run_id,
            status=StageRunStatus.SUCCEEDED,
            output_commit_sha=output_commit_sha,
            failure=None,
            finished_at=finished_at,
        )

    def mark_failed(
        self,
        connection: sqlite3.Connection,
        stage_run_id: str,
        *,
        failure: str,
        finished_at: datetime,
    ) -> StageRun:
        return self._finish(
            connection,
            stage_run_id=stage_run_id,
            status=StageRunStatus.FAILED,
            output_commit_sha=None,
            failure=failure,
            finished_at=finished_at,
        )

    def _finish(
        self,
        connection: sqlite3.Connection,
        *,
        stage_run_id: str,
        status: StageRunStatus,
        output_commit_sha: str | None,
        failure: str | None,
        finished_at: datetime,
    ) -> StageRun:
        row = connection.execute(
            f"""
            UPDATE stage_run
            SET
                status = ?,
                output_commit_sha = ?,
                failure = ?,
                lease_expires_at = NULL,
                finished_at = ?
            WHERE stage_run_id = ? AND status = ?
            RETURNING {self._COLUMNS}
            """,
            (
                status.value,
                output_commit_sha,
                failure,
                _encode_datetime(finished_at),
                stage_run_id,
                StageRunStatus.RUNNING.value,
            ),
        ).fetchone()
        if row is None:
            raise LookupError(f"Running StageRun not found: {stage_run_id}")
        return self._from_row(row)

    _COLUMNS = """
        stage_run_id,
        triage_id,
        run_id,
        snapshot_id,
        milestone_key,
        stage_key,
        status,
        input_commit_sha,
        output_commit_sha,
        failure,
        created_at,
        started_at,
        lease_expires_at,
        finished_at
    """
    _SELECT = f"SELECT {_COLUMNS} FROM stage_run"

    @staticmethod
    def _values(stage_run: StageRun) -> tuple[object, ...]:
        return (
            stage_run.stage_run_id,
            stage_run.triage_id,
            stage_run.run_id,
            stage_run.snapshot_id,
            stage_run.milestone_key,
            stage_run.stage_key,
            stage_run.status.value,
            stage_run.input_commit_sha,
            stage_run.output_commit_sha,
            stage_run.failure,
            _encode_datetime(stage_run.created_at),
            _encode_datetime(stage_run.started_at),
            _encode_datetime(stage_run.lease_expires_at),
            _encode_datetime(stage_run.finished_at),
        )

    @staticmethod
    def _from_row(row: sqlite3.Row) -> StageRun:
        return StageRun(
            stage_run_id=cast(str, row["stage_run_id"]),
            triage_id=cast(str, row["triage_id"]),
            run_id=cast(str, row["run_id"]),
            snapshot_id=cast(str, row["snapshot_id"]),
            milestone_key=cast(str, row["milestone_key"]),
            stage_key=cast(str, row["stage_key"]),
            status=StageRunStatus(cast(str, row["status"])),
            input_commit_sha=cast(str, row["input_commit_sha"]),
            output_commit_sha=cast(str | None, row["output_commit_sha"]),
            failure=cast(str | None, row["failure"]),
            created_at=_decode_datetime(cast(str, row["created_at"])),
            started_at=_decode_optional_datetime(row["started_at"]),
            lease_expires_at=_decode_optional_datetime(row["lease_expires_at"]),
            finished_at=_decode_optional_datetime(row["finished_at"]),
        )


def _encode_datetime(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _decode_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _decode_optional_datetime(value: object) -> datetime | None:
    return _decode_datetime(cast(str, value)) if value is not None else None
