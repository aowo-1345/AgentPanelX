"""SQLite persistence for the Project Owner activation mailbox."""

import sqlite3
from datetime import datetime
from typing import cast

from agentplanex.domains import (
    OwnerActivation,
    OwnerActivationMode,
    OwnerActivationStatus,
    ProjectOwnerTaskType,
)


class SQLiteOwnerActivationRepository:
    """Persist and atomically advance durable Owner activations."""

    def insert(
        self,
        connection: sqlite3.Connection,
        activation: OwnerActivation,
    ) -> None:
        connection.execute(
            """
            INSERT INTO owner_activation (
                activation_id,
                triage_id,
                task_type,
                message_id,
                summary_id,
                status,
                driver_mode,
                created_at,
                started_at,
                finished_at,
                failure
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            self._values(activation),
        )

    def get(
        self,
        connection: sqlite3.Connection,
        activation_id: str,
    ) -> OwnerActivation | None:
        row = connection.execute(
            f"{self._SELECT} WHERE activation_id = ?",
            (activation_id,),
        ).fetchone()
        return self._from_row(row) if row is not None else None

    def list_by_triage_id(
        self,
        connection: sqlite3.Connection,
        triage_id: str,
    ) -> tuple[OwnerActivation, ...]:
        rows = connection.execute(
            f"{self._SELECT} WHERE triage_id = ? ORDER BY created_at, activation_id",
            (triage_id,),
        ).fetchall()
        return tuple(self._from_row(row) for row in rows)

    def get_unfinished(
        self,
        connection: sqlite3.Connection,
        triage_id: str,
    ) -> OwnerActivation | None:
        row = connection.execute(
            f"""
            {self._SELECT}
            WHERE triage_id = ? AND status IN (?, ?)
            ORDER BY created_at, activation_id
            LIMIT 1
            """,
            (
                triage_id,
                OwnerActivationStatus.PENDING.value,
                OwnerActivationStatus.RUNNING.value,
            ),
        ).fetchone()
        return self._from_row(row) if row is not None else None

    def claim_next(
        self,
        connection: sqlite3.Connection,
        triage_id: str,
        started_at: datetime,
        driver_mode: OwnerActivationMode,
    ) -> OwnerActivation | None:
        """Claim the oldest pending activation when this Triage is idle.

        Callers must hold the database's immediate write transaction while invoking
        this method. The conditional update is still kept in one SQL statement so
        a future caller cannot accidentally split selection from ownership.
        """
        row = connection.execute(
            f"""
            UPDATE owner_activation
            SET status = ?, driver_mode = ?, started_at = COALESCE(started_at, ?)
            WHERE activation_id = (
                SELECT candidate.activation_id
                FROM owner_activation AS candidate
                WHERE candidate.triage_id = ?
                  AND candidate.status = ?
                  AND (
                      candidate.driver_mode IS NULL
                      OR (
                          ? = ?
                          AND candidate.driver_mode = ?
                      )
                  )
                  AND NOT EXISTS (
                      SELECT 1
                      FROM owner_activation AS running
                      WHERE running.triage_id = ?
                        AND running.status = ?
                  )
                ORDER BY candidate.created_at, candidate.activation_id
                LIMIT 1
            )
              AND status = ?
            RETURNING {self._COLUMNS}
            """,
            (
                OwnerActivationStatus.RUNNING.value,
                driver_mode.value,
                _encode_datetime(started_at),
                triage_id,
                OwnerActivationStatus.PENDING.value,
                driver_mode.value,
                OwnerActivationMode.TOOL.value,
                OwnerActivationMode.TOOL.value,
                triage_id,
                OwnerActivationStatus.RUNNING.value,
                OwnerActivationStatus.PENDING.value,
            ),
        ).fetchone()
        return self._from_row(row) if row is not None else None

    def mark_completed(
        self,
        connection: sqlite3.Connection,
        activation_id: str,
        finished_at: datetime,
    ) -> OwnerActivation:
        return self._finish(
            connection,
            activation_id=activation_id,
            status=OwnerActivationStatus.COMPLETED,
            finished_at=finished_at,
            failure=None,
        )

    def mark_failed(
        self,
        connection: sqlite3.Connection,
        activation_id: str,
        finished_at: datetime,
        failure: str,
    ) -> OwnerActivation:
        if not failure.strip():
            raise ValueError("Activation failure must not be empty")
        return self._finish(
            connection,
            activation_id=activation_id,
            status=OwnerActivationStatus.FAILED,
            finished_at=finished_at,
            failure=failure,
        )

    def _finish(
        self,
        connection: sqlite3.Connection,
        *,
        activation_id: str,
        status: OwnerActivationStatus,
        finished_at: datetime,
        failure: str | None,
    ) -> OwnerActivation:
        row = connection.execute(
            f"""
            UPDATE owner_activation
            SET status = ?, finished_at = ?, failure = ?
            WHERE activation_id = ? AND status = ?
            RETURNING {self._COLUMNS}
            """,
            (
                status.value,
                _encode_datetime(finished_at),
                failure,
                activation_id,
                OwnerActivationStatus.RUNNING.value,
            ),
        ).fetchone()
        if row is None:
            raise LookupError(
                f"Running Owner activation not found: {activation_id}"
            )
        return self._from_row(row)

    def release_tool(
        self,
        connection: sqlite3.Connection,
        activation_id: str,
    ) -> OwnerActivation:
        """Release a non-terminal manual step for the next explicit Tool Action."""

        row = connection.execute(
            f"""
            UPDATE owner_activation
            SET status = ?
            WHERE activation_id = ?
              AND status = ?
              AND driver_mode = ?
            RETURNING {self._COLUMNS}
            """,
            (
                OwnerActivationStatus.PENDING.value,
                activation_id,
                OwnerActivationStatus.RUNNING.value,
                OwnerActivationMode.TOOL.value,
            ),
        ).fetchone()
        if row is None:
            raise LookupError(
                f"Running Tool Owner activation not found: {activation_id}"
            )
        return self._from_row(row)

    _COLUMNS = """
        activation_id,
        triage_id,
        task_type,
        message_id,
        summary_id,
        status,
        driver_mode,
        created_at,
        started_at,
        finished_at,
        failure
    """
    _SELECT = f"SELECT {_COLUMNS} FROM owner_activation"

    @staticmethod
    def _values(activation: OwnerActivation) -> tuple[object, ...]:
        return (
            activation.activation_id,
            activation.triage_id,
            activation.task_type.value,
            activation.message_id,
            activation.summary_id,
            activation.status.value,
            activation.driver_mode.value if activation.driver_mode is not None else None,
            _encode_datetime(activation.created_at),
            _encode_datetime(activation.started_at),
            _encode_datetime(activation.finished_at),
            activation.failure,
        )

    @staticmethod
    def _from_row(row: sqlite3.Row) -> OwnerActivation:
        return OwnerActivation(
            activation_id=cast(str, row["activation_id"]),
            triage_id=cast(str, row["triage_id"]),
            task_type=ProjectOwnerTaskType(cast(str, row["task_type"])),
            message_id=cast(str, row["message_id"]),
            summary_id=cast(str | None, row["summary_id"]),
            status=OwnerActivationStatus(cast(str, row["status"])),
            driver_mode=(
                OwnerActivationMode(cast(str, row["driver_mode"]))
                if row["driver_mode"] is not None
                else None
            ),
            created_at=_decode_datetime(cast(str, row["created_at"])),
            started_at=_decode_optional_datetime(row["started_at"]),
            finished_at=_decode_optional_datetime(row["finished_at"]),
            failure=cast(str | None, row["failure"]),
        )


def _encode_datetime(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _decode_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _decode_optional_datetime(value: object) -> datetime | None:
    return _decode_datetime(cast(str, value)) if value is not None else None
