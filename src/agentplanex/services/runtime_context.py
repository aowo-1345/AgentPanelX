"""Unified project Runtime Context transitions and observable diffs."""

import sqlite3
from dataclasses import dataclass, field

from agentplanex.domains import (
    ProjectRuntimeState,
    RuntimeContextChangeReason,
)
from agentplanex.infrastructure.sqlite import SQLiteDatabase
from agentplanex.infrastructure.sqlite.repositories import (
    SQLiteProjectRuntimeStateRepository,
)
from agentplanex.services.event_bus import EventBus
from agentplanex.services.project_runtime_context._state import (
    StateMutation,
    StateTransition,
    apply_state_transition,
)


@dataclass(slots=True)
class RuntimeContextService:
    database: SQLiteDatabase
    event_bus: EventBus
    contexts: SQLiteProjectRuntimeStateRepository = field(
        default_factory=SQLiteProjectRuntimeStateRepository
    )

    def get(self, triage_id: str) -> ProjectRuntimeState | None:
        with self.database.connection() as connection:
            return self.contexts.get(connection, triage_id)

    def transition(
        self,
        triage_id: str,
        *,
        reason: RuntimeContextChangeReason,
        mutate: StateMutation,
    ) -> ProjectRuntimeState:
        with self.database.transaction() as connection:
            updated, event = self.transition_in_transaction(
                connection,
                triage_id,
                reason=reason,
                mutate=mutate,
            )

        if event is not None:
            self.event_bus.publish(event)
        return updated

    def transition_in_transaction(
        self,
        connection: sqlite3.Connection,
        triage_id: str,
        *,
        reason: RuntimeContextChangeReason,
        mutate: StateMutation,
    ) -> StateTransition:
        """Persist one transition inside a caller-owned transaction.

        The returned event must be published only after that transaction commits;
        Timeline handlers use a separate SQLite connection by design.
        """
        current = self.contexts.get(connection, triage_id)
        if current is None:
            raise LookupError(f"Project Runtime Context not found: {triage_id}")
        return apply_state_transition(
            connection,
            self.contexts,
            current,
            reason=reason,
            mutate=mutate,
        )
