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

    def list_between_checkpoints(
        self,
        connection: sqlite3.Connection,
        session_id: str,
        *,
        after_message_id: str | None,
        through_message_id: str,
    ) -> tuple[MessageHistory, ...]:
        """Return one session's ordered batches after a watermark through a trigger."""

        trigger = self._require_session_message(
            connection,
            session_id,
            through_message_id,
            checkpoint_name="Activation message",
        )
        after_sequence = 0
        if after_message_id is not None:
            watermark = self._require_session_message(
                connection,
                session_id,
                after_message_id,
                checkpoint_name="Summary watermark",
            )
            if watermark.sequence >= trigger.sequence:
                raise ValueError(
                    "Summary watermark must precede activation message: "
                    f"{watermark.message_id} !< {trigger.message_id}"
                )
            after_sequence = watermark.sequence

        rows = connection.execute(
            """
            SELECT project_owner_session_id, message_id, sequence, message
            FROM message_history
            WHERE project_owner_session_id = ?
              AND sequence > ?
              AND sequence <= ?
            ORDER BY sequence
            """,
            (session_id, after_sequence, trigger.sequence),
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

    def get_latest_by_session_id(
        self,
        connection: sqlite3.Connection,
        session_id: str,
    ) -> MessageHistory | None:
        row = connection.execute(
            """
            SELECT project_owner_session_id, message_id, sequence, message
            FROM message_history
            WHERE project_owner_session_id = ?
            ORDER BY sequence DESC
            LIMIT 1
            """,
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        return MessageHistory(
            project_owner_session_id=cast(str, row["project_owner_session_id"]),
            message_id=cast(str, row["message_id"]),
            sequence=cast(int, row["sequence"]),
            message=self._decode_messages(cast(str, row["message"])),
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

    def _require_session_message(
        self,
        connection: sqlite3.Connection,
        session_id: str,
        message_id: str,
        *,
        checkpoint_name: str,
    ) -> MessageHistory:
        history = self.get(connection, message_id)
        if history is None:
            raise LookupError(f"{checkpoint_name} not found: {message_id}")
        if history.project_owner_session_id != session_id:
            raise ValueError(
                f"{checkpoint_name} does not belong to Owner session: {message_id}"
            )
        return history

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
