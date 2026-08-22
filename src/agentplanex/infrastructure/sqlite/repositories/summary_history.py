"""SQLite operations for Project Owner Agent summary history."""

import sqlite3
from typing import cast

from agentplanex.project_owner_agent.context.models import SummaryHistory


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
                intent_summary_content,
                trajectory_summary_content
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                summary.project_owner_session_id,
                summary.summary_id,
                summary.covered_through_message_id,
                summary.intent_summary_content,
                summary.trajectory_summary_content,
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
                intent_summary_content,
                trajectory_summary_content
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
            covered_through_message_id=cast(str, row["covered_through_message_id"]),
            intent_summary_content=cast(str, row["intent_summary_content"]),
            trajectory_summary_content=cast(str, row["trajectory_summary_content"]),
        )

    def latest_through_message(
        self,
        connection: sqlite3.Connection,
        session_id: str,
        through_sequence: int,
    ) -> SummaryHistory | None:
        row = connection.execute(
            """
            SELECT
                summaries.project_owner_session_id,
                summaries.summary_id,
                summaries.covered_through_message_id,
                summaries.intent_summary_content,
                summaries.trajectory_summary_content
            FROM summary_history AS summaries
            JOIN message_history AS watermark
              ON watermark.message_id = summaries.covered_through_message_id
             AND watermark.project_owner_session_id = summaries.project_owner_session_id
            WHERE summaries.project_owner_session_id = ?
              AND watermark.sequence <= ?
            ORDER BY watermark.sequence DESC
            LIMIT 1
            """,
            (session_id, through_sequence),
        ).fetchone()
        if row is None:
            return None
        return SummaryHistory(
            project_owner_session_id=cast(str, row["project_owner_session_id"]),
            summary_id=cast(str, row["summary_id"]),
            covered_through_message_id=cast(str, row["covered_through_message_id"]),
            intent_summary_content=cast(str, row["intent_summary_content"]),
            trajectory_summary_content=cast(str, row["trajectory_summary_content"]),
        )
