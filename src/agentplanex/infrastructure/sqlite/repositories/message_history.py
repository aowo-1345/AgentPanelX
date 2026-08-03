"""SQLite operations for Project Owner Agent message history."""

import json
import sqlite3
from typing import cast

from agentplanex.domains import Message, MessageHistory


class SQLiteMessageHistoryRepository:
    """Append and query immutable message-history entries."""

    def insert(
        self,
        connection: sqlite3.Connection,
        history: MessageHistory,
    ) -> None:
        connection.execute(
            """
            INSERT INTO message_history (
                project_owner_session_id,
                message_id,
                sequence,
                message
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                history.project_owner_session_id,
                history.message_id,
                history.sequence,
                json.dumps(history.message, ensure_ascii=True, separators=(",", ":")),
            ),
        )

    def get(
        self,
        connection: sqlite3.Connection,
        message_id: str,
    ) -> MessageHistory | None:
        row = connection.execute(
            """
            SELECT project_owner_session_id, message_id, sequence, message
            FROM message_history
            WHERE message_id = ?
            """,
            (message_id,),
        ).fetchone()
        if row is None:
            return None
        return MessageHistory(
            project_owner_session_id=cast(str, row["project_owner_session_id"]),
            message_id=cast(str, row["message_id"]),
            sequence=cast(int, row["sequence"]),
            message=self._decode_messages(cast(str, row["message"])),
        )

    def list_by_session_id(
        self,
        connection: sqlite3.Connection,
        session_id: str,
    ) -> tuple[MessageHistory, ...]:
        rows = connection.execute(
            """
            SELECT project_owner_session_id, message_id, sequence, message
            FROM message_history
            WHERE project_owner_session_id = ?
            ORDER BY sequence
            """,
            (session_id,),
        ).fetchall()
        return tuple(
            MessageHistory(
                project_owner_session_id=cast(str, row["project_owner_session_id"]),
                message_id=cast(str, row["message_id"]),
                sequence=cast(int, row["sequence"]),
                message=self._decode_messages(cast(str, row["message"])),
            )
            for row in rows
        )

    def next_sequence(
        self,
        connection: sqlite3.Connection,
        session_id: str,
    ) -> int:
        row = connection.execute(
            """
            SELECT COALESCE(MAX(sequence), 0) + 1 AS sequence
            FROM message_history
            WHERE project_owner_session_id = ?
            """,
            (session_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError("SQLite did not return the next message-history sequence")
        return cast(int, row["sequence"])

    @staticmethod
    def _decode_messages(value: str) -> tuple[Message, ...]:
        decoded: object = json.loads(value)
        if not isinstance(decoded, list):
            raise ValueError("Stored message history must be a JSON array")

        messages: list[Message] = []
        for item in decoded:
            if not isinstance(item, dict) or not all(
                isinstance(key, str) for key in item
            ):
                raise ValueError("Stored messages must be JSON objects")
            messages.append(cast(Message, item))
        return tuple(messages)
