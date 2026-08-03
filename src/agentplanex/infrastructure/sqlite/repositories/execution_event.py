"""SQLite operations for the project execution Timeline."""

import json
import sqlite3
from datetime import datetime
from typing import cast

from agentplanex.domains import ExecutionEvent, ExecutionEventType


class SQLiteExecutionEventRepository:
    def insert(
        self,
        connection: sqlite3.Connection,
        event: ExecutionEvent,
    ) -> int:
        cursor = connection.execute(
            """
            INSERT INTO execution_event (
                triage_id,
                event_type,
                react_loop_id,
                message_id,
                payload,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                event.triage_id,
                event.event_type.value,
                event.react_loop_id,
                event.message_id,
                json.dumps(event.payload, ensure_ascii=True, separators=(",", ":")),
                event.created_at.isoformat(),
            ),
        )
        if cursor.lastrowid is None:
            raise RuntimeError("SQLite did not return an execution event ID")
        return cursor.lastrowid

    def list_by_triage_id(
        self,
        connection: sqlite3.Connection,
        triage_id: str,
    ) -> tuple[ExecutionEvent, ...]:
        rows = connection.execute(
            f"{self._SELECT} WHERE triage_id = ? ORDER BY event_id",
            (triage_id,),
        ).fetchall()
        return tuple(self._from_row(row) for row in rows)

    def get_active_react_loop_id(
        self,
        connection: sqlite3.Connection,
        triage_id: str,
    ) -> str | None:
        row = connection.execute(
            """
            SELECT entered.react_loop_id
            FROM execution_event AS entered
            WHERE entered.triage_id = ?
              AND entered.event_type = ?
              AND entered.react_loop_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1
                  FROM execution_event AS exited
                  WHERE exited.triage_id = entered.triage_id
                    AND exited.event_type = ?
                    AND exited.react_loop_id = entered.react_loop_id
              )
            ORDER BY entered.event_id DESC
            LIMIT 1
            """,
            (
                triage_id,
                ExecutionEventType.REACT_LOOP_ENTERED.value,
                ExecutionEventType.REACT_LOOP_EXITED.value,
            ),
        ).fetchone()
        return cast(str, row["react_loop_id"]) if row is not None else None

    _SELECT = """
        SELECT
            event_id,
            triage_id,
            event_type,
            react_loop_id,
            message_id,
            payload,
            created_at
        FROM execution_event
    """

    @staticmethod
    def _from_row(row: sqlite3.Row) -> ExecutionEvent:
        payload: object = json.loads(cast(str, row["payload"]))
        if not isinstance(payload, dict) or not all(
            isinstance(key, str) for key in payload
        ):
            raise ValueError("Stored execution event payload must be a JSON object")
        return ExecutionEvent(
            event_id=cast(int, row["event_id"]),
            triage_id=cast(str, row["triage_id"]),
            event_type=ExecutionEventType(cast(str, row["event_type"])),
            react_loop_id=cast(str | None, row["react_loop_id"]),
            message_id=cast(str | None, row["message_id"]),
            payload=cast(dict[str, object], payload),
            created_at=datetime.fromisoformat(cast(str, row["created_at"])),
        )
