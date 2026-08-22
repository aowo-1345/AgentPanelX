"""SQLite-backed handler for observable project execution events."""

from dataclasses import dataclass, field, replace

from agentplanex.domains.execution_event import ExecutionEvent
from agentplanex.infrastructure.sqlite.database import SQLiteDatabase
from agentplanex.infrastructure.sqlite.repositories.execution_event import (
    SQLiteExecutionEventRepository,
)
from agentplanex.infrastructure.sqlite.repositories.project_owner_agent import (
    SQLiteProjectOwnerAgentRepository,
)


@dataclass(slots=True)
class SQLiteTimelineRecorder:
    database: SQLiteDatabase
    events: SQLiteExecutionEventRepository = field(
        default_factory=SQLiteExecutionEventRepository
    )
    owners: SQLiteProjectOwnerAgentRepository = field(
        default_factory=SQLiteProjectOwnerAgentRepository
    )

    def __call__(self, event: ExecutionEvent) -> None:
        with self.database.transaction() as connection:
            owner = self.owners.get_by_triage_id(connection, event.triage_id)
            message_id = owner.message_id if owner is not None else None
            react_loop_id = event.react_loop_id
            if react_loop_id is None:
                react_loop_id = self.events.get_active_react_loop_id(
                    connection,
                    event.triage_id,
                )
            self.events.insert(
                connection,
                replace(
                    event,
                    message_id=message_id,
                    react_loop_id=react_loop_id,
                ),
            )
