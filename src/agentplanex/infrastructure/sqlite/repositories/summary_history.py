"""SQLite operations for Project Owner Agent summary history."""

import sqlite3
from typing import cast

from agentplanex.domains import SummaryHistory


class SQLiteSummaryHistoryRepository:
    """Append and query immutable summary-history entries."""

    def insert(
        self,
        connection: sqlite3.Connection,
        summary: SummaryHistory,
    ) -> None:
        connection.execute(
            """
            INSERT INTO summary_history (
                project_owner_session_id,
                summary_id,
                covered_through_message_id,
                summary_content
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                summary.project_owner_session_id,
                summary.summary_id,
                summary.covered_through_message_id,
                summary.summary_content,
            ),
        )

    def get(
        self,
        connection: sqlite3.Connection,
        summary_id: str,
    ) -> SummaryHistory | None:
        row = connection.execute(
            """
            SELECT
                project_owner_session_id,
                summary_id,
                covered_through_message_id,
                summary_content
            FROM summary_history
            WHERE summary_id = ?
            """,
            (summary_id,),
        ).fetchone()
        if row is None:
            return None
        return SummaryHistory(
            project_owner_session_id=cast(str, row["project_owner_session_id"]),
            summary_id=cast(str, row["summary_id"]),
            summary_content=cast(str, row["summary_content"]),
            covered_through_message_id=cast(
                str | None,
                row["covered_through_message_id"],
            ),
        )
