"""Read-only Project Owner context reconstruction by message checkpoint."""

import sqlite3
from dataclasses import dataclass, field

from agentplanex.domains import Message, RestoredOwnerContext, SummaryHistory
from agentplanex.infrastructure.sqlite import SQLiteDatabase
from agentplanex.infrastructure.sqlite.repositories import (
    SQLiteMessageHistoryRepository,
    SQLiteProjectOwnerAgentRepository,
    SQLiteSummaryHistoryRepository,
)


@dataclass(slots=True)
class ProjectOwnerContextQuery:
    """Reconstruct bounded Owner input without changing Runtime state."""

    database: SQLiteDatabase
    summary_context_header: str
    owners: SQLiteProjectOwnerAgentRepository = field(
        default_factory=SQLiteProjectOwnerAgentRepository
    )
    messages: SQLiteMessageHistoryRepository = field(
        default_factory=SQLiteMessageHistoryRepository
    )
    summaries: SQLiteSummaryHistoryRepository = field(
        default_factory=SQLiteSummaryHistoryRepository
    )

    def restore(
        self,
        through_message_id: str,
        *,
        summary_id: str | None = None,
    ) -> RestoredOwnerContext:
        """Restore full history or one explicitly selected Summary projection."""

        with self.database.read_only_connection() as connection:
            return self.restore_in_connection(
                connection,
                through_message_id,
                summary_id=summary_id,
            )

    def latest_summary_id_through(self, through_message_id: str) -> str | None:
        """Resolve an Attribution checkpoint without changing raw restore defaults."""

        checkpoint_id = through_message_id.strip()
        if not checkpoint_id:
            raise ValueError("through_message_id must not be empty")
        with self.database.read_only_connection() as connection:
            through = self.messages.get(connection, checkpoint_id)
            if through is None:
                raise LookupError(f"Message checkpoint not found: {checkpoint_id}")
            summary = self.summaries.latest_through_message(
                connection,
                through.project_owner_session_id,
                through.sequence,
            )
        return summary.summary_id if summary is not None else None

    def restore_in_connection(
        self,
        connection: sqlite3.Connection,
        through_message_id: str,
        *,
        summary_id: str | None = None,
    ) -> RestoredOwnerContext:
        """Restore through a checkpoint inside the caller's read snapshot."""

        checkpoint_id = through_message_id.strip()
        if not checkpoint_id:
            raise ValueError("through_message_id must not be empty")
        selected_summary_id = summary_id.strip() if summary_id is not None else None
        if selected_summary_id == "":
            raise ValueError("summary_id must not be empty")

        through = self.messages.get(connection, checkpoint_id)
        if through is None:
            raise LookupError(f"Message checkpoint not found: {checkpoint_id}")
        owner = self.owners.get_by_session_id(
            connection,
            through.project_owner_session_id,
        )
        if owner is None:
            raise LookupError(
                "Project Owner Agent not found for message checkpoint: "
                f"{checkpoint_id}"
            )

        summary = self._load_summary(
            connection,
            owner.project_owner_session_id,
            selected_summary_id,
        )
        histories = self.messages.list_between_checkpoints(
            connection,
            owner.project_owner_session_id,
            after_message_id=(
                summary.covered_through_message_id if summary is not None else None
            ),
            through_message_id=checkpoint_id,
        )
        covered_through_sequence = self._covered_through_sequence(
            connection,
            summary,
        )
        restored_messages: list[Message] = [
            {"role": "system", "content": owner.system_prompt}
        ]
        if summary is not None:
            restored_messages.extend(
                render_summary_messages(summary, self.summary_context_header)
            )
        restored_messages.extend(
            dict(message)
            for history in histories
            for message in history.message
            if message.get("role") != "system"
        )
        return RestoredOwnerContext(
            triage_id=owner.triage_id,
            project_owner_session_id=owner.project_owner_session_id,
            through_message_id=through.message_id,
            through_sequence=through.sequence,
            summary_id=summary.summary_id if summary is not None else None,
            intent_summary_content=(
                summary.intent_summary_content if summary is not None else None
            ),
            trajectory_summary_content=(
                summary.trajectory_summary_content if summary is not None else None
            ),
            covered_through_message_id=(
                summary.covered_through_message_id if summary is not None else None
            ),
            covered_through_sequence=covered_through_sequence,
            system_prompt=owner.system_prompt,
            tools=owner.tools,
            messages=tuple(restored_messages),
        )

    def _load_summary(
        self,
        connection: sqlite3.Connection,
        session_id: str,
        summary_id: str | None,
    ) -> SummaryHistory | None:
        if summary_id is None:
            return None
        summary = self.summaries.get(connection, summary_id)
        if summary is None:
            raise LookupError(f"Summary not found: {summary_id}")
        if summary.project_owner_session_id != session_id:
            raise ValueError(
                f"Summary does not belong to Owner session: {summary_id}"
            )
        return summary

    def _covered_through_sequence(
        self,
        connection: sqlite3.Connection,
        summary: SummaryHistory | None,
    ) -> int | None:
        if summary is None:
            return None
        watermark = self.messages.get(
            connection,
            summary.covered_through_message_id,
        )
        if watermark is None:
            raise LookupError(
                "Summary watermark not found: "
                f"{summary.covered_through_message_id}"
            )
        return watermark.sequence


def render_summary_messages(
    summary: SummaryHistory,
    header: str,
) -> tuple[Message, Message]:
    """Render one immutable Summary as two model-visible context messages."""

    return (
        {"role": "developer", "content": header.strip()},
        {
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": (
                        "<intent-summary>\n"
                        f"{summary.intent_summary_content}\n"
                        "</intent-summary>"
                    ),
                },
                {
                    "type": "input_text",
                    "text": (
                        "<trajectory-summary>\n"
                        f"{summary.trajectory_summary_content}\n"
                        "</trajectory-summary>"
                    ),
                },
            ],
        },
    )
