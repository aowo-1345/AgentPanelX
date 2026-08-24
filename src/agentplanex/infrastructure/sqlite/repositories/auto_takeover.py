"""SQLite persistence and fencing for AutoTakeover business runs."""

import json
import sqlite3
from datetime import UTC, datetime
from uuid import uuid4

from agentplanex.domains.artifact import ArtifactDescriptor
from agentplanex.services.auto_takeover.models import (
    AttemptStatus,
    TakeoverAttempt,
    TakeoverRun,
    TakeoverStatus,
)


class AutoTakeoverFenceError(ValueError):
    """A mutation did not carry the currently active takeover fence."""


class SQLiteAutoTakeoverRepository:
    """Persist one active Run per Feature and one active fenced Attempt."""

    def begin(
        self,
        connection: sqlite3.Connection,
        *,
        triage_id: str,
        trigger_event_id: int,
    ) -> tuple[TakeoverRun, TakeoverAttempt] | None:
        existing = self.get_by_trigger(connection, trigger_event_id)
        if existing is not None:
            return None
        if self.get_active(connection, triage_id) is not None:
            return None
        now = datetime.now(UTC)
        run = TakeoverRun(
            run_id=uuid4().hex,
            triage_id=triage_id,
            trigger_event_id=trigger_event_id,
            status=TakeoverStatus.RUNNING,
            decision=None,
            attribution=None,
            error=None,
            started_at=now,
            finished_at=None,
        )
        connection.execute(
            """
            INSERT INTO auto_takeover_run (
                run_id, triage_id, trigger_event_id, status, decision,
                attribution, error, started_at, finished_at
            ) VALUES (?, ?, ?, ?, NULL, NULL, NULL, ?, NULL)
            """,
            (run.run_id, run.triage_id, run.trigger_event_id, run.status.value, now.isoformat()),
        )
        attempt = self._insert_attempt(connection, run.run_id, 1, now)
        return run, attempt

    def correct(
        self,
        connection: sqlite3.Connection,
        run_id: str,
        error: str,
    ) -> TakeoverAttempt:
        current = self.get_active_attempt(connection, run_id)
        if current is None or current.ordinal != 1:
            raise ValueError("AutoTakeover correction is no longer available")
        now = datetime.now(UTC)
        connection.execute(
            """
            UPDATE auto_takeover_attempt
            SET status = 'INVALID', fence_token = NULL, error = ?, finished_at = ?
            WHERE attempt_id = ? AND status = 'RUNNING'
            """,
            (error, now.isoformat(), current.attempt_id),
        )
        return self._insert_attempt(connection, run_id, 2, now)

    def complete(
        self,
        connection: sqlite3.Connection,
        run_id: str,
        status: TakeoverStatus,
        *,
        attribution: ArtifactDescriptor | None = None,
        error: str | None = None,
    ) -> TakeoverRun:
        if status is TakeoverStatus.RUNNING:
            raise ValueError("AutoTakeover cannot complete as RUNNING")
        current = self.get_active_attempt(connection, run_id)
        now = datetime.now(UTC)
        if current is not None:
            attempt_status = (
                AttemptStatus.COMPLETED
                if status in {TakeoverStatus.YES, TakeoverStatus.NO}
                else AttemptStatus.FAILED
            )
            connection.execute(
                """
                UPDATE auto_takeover_attempt
                SET status = ?, fence_token = NULL, error = ?, finished_at = ?
                WHERE attempt_id = ? AND status = 'RUNNING'
                """,
                (attempt_status.value, error, now.isoformat(), current.attempt_id),
            )
        decision = status.value if status in {TakeoverStatus.YES, TakeoverStatus.NO} else None
        encoded = (
            json.dumps(
                {
                    "uri": attribution.uri,
                    "project_relative_path": attribution.project_relative_path,
                    "media_type": attribution.media_type,
                    "size": attribution.size,
                    "sha256": attribution.sha256,
                },
                sort_keys=True,
            )
            if attribution is not None
            else None
        )
        cursor = connection.execute(
            """
            UPDATE auto_takeover_run
            SET status = ?, decision = ?, attribution = ?, error = ?, finished_at = ?
            WHERE run_id = ? AND status = 'RUNNING'
            """,
            (status.value, decision, encoded, error, now.isoformat(), run_id),
        )
        if cursor.rowcount != 1:
            raise ValueError("AutoTakeover Run is not active")
        completed = self.get(connection, run_id)
        assert completed is not None
        return completed

    def require_active_fence(
        self,
        connection: sqlite3.Connection,
        triage_id: str,
        token: str | None,
    ) -> None:
        active = self.get_active(connection, triage_id)
        if active is None:
            if token is not None:
                raise AutoTakeoverFenceError("AutoTakeover fence is no longer active")
            return
        attempt = self.get_active_attempt(connection, active.run_id)
        if attempt is None or token is None or attempt.fence_token != token:
            raise AutoTakeoverFenceError("Active AutoTakeover requires its current fence")

    def require_mutation_fence(
        self,
        connection: sqlite3.Connection,
        token: str | None,
    ) -> None:
        """Authorize one project-local mutation inside its existing transaction."""
        row = connection.execute(
            "SELECT triage_id FROM auto_takeover_run WHERE status = 'RUNNING'"
        ).fetchone()
        if row is None:
            if token is not None:
                raise AutoTakeoverFenceError("AutoTakeover fence is no longer active")
            return
        self.require_active_fence(connection, row["triage_id"], token)

    def get(self, connection: sqlite3.Connection, run_id: str) -> TakeoverRun | None:
        row = connection.execute(
            "SELECT * FROM auto_takeover_run WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        return _run(row) if row is not None else None

    def get_by_trigger(
        self,
        connection: sqlite3.Connection,
        trigger_event_id: int,
    ) -> TakeoverRun | None:
        row = connection.execute(
            "SELECT * FROM auto_takeover_run WHERE trigger_event_id = ?",
            (trigger_event_id,),
        ).fetchone()
        return _run(row) if row is not None else None

    def get_active(
        self,
        connection: sqlite3.Connection,
        triage_id: str,
    ) -> TakeoverRun | None:
        row = connection.execute(
            """
            SELECT * FROM auto_takeover_run
            WHERE triage_id = ? AND status = 'RUNNING'
            """,
            (triage_id,),
        ).fetchone()
        return _run(row) if row is not None else None

    def latest(
        self,
        connection: sqlite3.Connection,
        triage_id: str,
    ) -> TakeoverRun | None:
        row = connection.execute(
            """
            SELECT * FROM auto_takeover_run
            WHERE triage_id = ? ORDER BY started_at DESC, run_id DESC LIMIT 1
            """,
            (triage_id,),
        ).fetchone()
        return _run(row) if row is not None else None

    def list_reports(
        self,
        connection: sqlite3.Connection,
        triage_id: str,
        *,
        limit: int,
    ) -> tuple[TakeoverRun, ...]:
        """Return bounded newest-first Runs that produced Attribution artifacts."""
        rows = connection.execute(
            """
            SELECT * FROM auto_takeover_run
            WHERE triage_id = ? AND attribution IS NOT NULL
            ORDER BY started_at DESC, run_id DESC
            LIMIT ?
            """,
            (triage_id, limit),
        ).fetchall()
        return tuple(_run(row) for row in rows)

    def get_active_attempt(
        self,
        connection: sqlite3.Connection,
        run_id: str,
    ) -> TakeoverAttempt | None:
        row = connection.execute(
            """
            SELECT * FROM auto_takeover_attempt
            WHERE run_id = ? AND status = 'RUNNING'
            """,
            (run_id,),
        ).fetchone()
        return _attempt(row) if row is not None else None

    def _insert_attempt(
        self,
        connection: sqlite3.Connection,
        run_id: str,
        ordinal: int,
        now: datetime,
    ) -> TakeoverAttempt:
        attempt = TakeoverAttempt(
            attempt_id=uuid4().hex,
            run_id=run_id,
            ordinal=ordinal,
            status=AttemptStatus.RUNNING,
            fence_token=uuid4().hex,
            error=None,
            started_at=now,
            finished_at=None,
        )
        connection.execute(
            """
            INSERT INTO auto_takeover_attempt (
                attempt_id, run_id, ordinal, status, fence_token,
                error, started_at, finished_at
            ) VALUES (?, ?, ?, 'RUNNING', ?, NULL, ?, NULL)
            """,
            (
                attempt.attempt_id,
                attempt.run_id,
                attempt.ordinal,
                attempt.fence_token,
                attempt.started_at.isoformat(),
            ),
        )
        return attempt


def _run(row: sqlite3.Row) -> TakeoverRun:
    attribution_payload = json.loads(row["attribution"]) if row["attribution"] else None
    return TakeoverRun(
        run_id=row["run_id"],
        triage_id=row["triage_id"],
        trigger_event_id=row["trigger_event_id"],
        status=TakeoverStatus(row["status"]),
        decision=row["decision"],
        attribution=(
            ArtifactDescriptor(**attribution_payload)
            if isinstance(attribution_payload, dict)
            else None
        ),
        error=row["error"],
        started_at=datetime.fromisoformat(row["started_at"]),
        finished_at=(datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None),
    )


def _attempt(row: sqlite3.Row) -> TakeoverAttempt:
    return TakeoverAttempt(
        attempt_id=row["attempt_id"],
        run_id=row["run_id"],
        ordinal=row["ordinal"],
        status=AttemptStatus(row["status"]),
        fence_token=row["fence_token"],
        error=row["error"],
        started_at=datetime.fromisoformat(row["started_at"]),
        finished_at=(datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None),
    )
