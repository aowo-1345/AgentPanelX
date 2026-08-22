"""Read-only restoration of immutable Project Owner history checkpoints."""

import sqlite3

from agentplanex.infrastructure.sqlite import SQLiteDatabase
from agentplanex.infrastructure.sqlite.repositories import (
    SQLiteMessageHistoryRepository,
    SQLiteProjectOwnerAgentRepository,
    SQLiteSummaryHistoryRepository,
)
from agentplanex.project_owner_agent.context.manager import OwnerContextSnapshot
from agentplanex.project_owner_agent.context.models import SummaryHistory


def restore_owner_context(
    database: SQLiteDatabase,
    through_message_id: str,
    *,
    summary_id: str | None = None,
) -> OwnerContextSnapshot:
    """Select raw persisted Owner facts through one immutable checkpoint."""

    with database.read_only_connection() as connection:
        return select_owner_context_snapshot(
            connection,
            through_message_id,
            summary_id=summary_id,
        )


def select_owner_context_snapshot(
    connection: sqlite3.Connection,
    through_message_id: str,
    *,
    summary_id: str | None = None,
) -> OwnerContextSnapshot:
    """Select only facts bounded by an immutable message checkpoint."""

    checkpoint_id = through_message_id.strip()
    if not checkpoint_id:
        raise ValueError("through_message_id must not be empty")
    selected_summary_id = summary_id.strip() if summary_id is not None else None
    if selected_summary_id == "":
        raise ValueError("summary_id must not be empty")

    owners = SQLiteProjectOwnerAgentRepository()
    messages = SQLiteMessageHistoryRepository()
    summaries = SQLiteSummaryHistoryRepository()
    through = messages.get(connection, checkpoint_id)
    if through is None:
        raise LookupError(f"Message checkpoint not found: {checkpoint_id}")
    owner = owners.get_by_session_id(
        connection,
        through.project_owner_session_id,
    )
    if owner is None:
        raise LookupError(
            "Project Owner Agent not found for message checkpoint: "
            f"{checkpoint_id}"
        )

    summary: SummaryHistory | None = None
    if selected_summary_id is not None:
        summary = summaries.get(connection, selected_summary_id)
        if summary is None:
            raise LookupError(f"Summary not found: {selected_summary_id}")
        if summary.project_owner_session_id != owner.project_owner_session_id:
            raise ValueError(
                "Summary does not belong to Owner session: "
                f"{selected_summary_id}"
            )
    histories = messages.list_between_checkpoints(
        connection,
        owner.project_owner_session_id,
        after_message_id=(
            summary.covered_through_message_id if summary is not None else None
        ),
        through_message_id=checkpoint_id,
    )
    covered_through_sequence: int | None = None
    if summary is not None:
        watermark = messages.get(
            connection,
            summary.covered_through_message_id,
        )
        if watermark is None:
            raise LookupError(
                "Summary watermark not found: "
                f"{summary.covered_through_message_id}"
            )
        covered_through_sequence = watermark.sequence

    return OwnerContextSnapshot(
        triage_id=owner.triage_id,
        project_owner_session_id=owner.project_owner_session_id,
        through_message_id=through.message_id,
        through_sequence=through.sequence,
        system_prompt=owner.system_prompt,
        tools=owner.tools,
        summary=summary,
        covered_through_sequence=covered_through_sequence,
        message_history=histories,
    )


def latest_owner_summary_id_through(
    database: SQLiteDatabase,
    through_message_id: str,
) -> str | None:
    """Resolve the newest Summary whose watermark does not pass a checkpoint."""

    checkpoint_id = through_message_id.strip()
    if not checkpoint_id:
        raise ValueError("through_message_id must not be empty")
    messages = SQLiteMessageHistoryRepository()
    summaries = SQLiteSummaryHistoryRepository()
    with database.read_only_connection() as connection:
        through = messages.get(connection, checkpoint_id)
        if through is None:
            raise LookupError(f"Message checkpoint not found: {checkpoint_id}")
        summary = summaries.latest_through_message(
            connection,
            through.project_owner_session_id,
            through.sequence,
        )
    return summary.summary_id if summary is not None else None
