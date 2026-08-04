"""Unified project Runtime Context transitions and observable diffs."""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field, fields
from datetime import datetime

from agentplanex.domains import (
    ExecutionEvent,
    ExecutionEventType,
    ProjectRuntimeContext,
    RuntimeContextChangeReason,
)
from agentplanex.infrastructure.sqlite import SQLiteDatabase
from agentplanex.infrastructure.sqlite.repositories import (
    SQLiteProjectRuntimeContextRepository,
)
from agentplanex.services.event_bus import EventBus

type ContextMutation = Callable[[ProjectRuntimeContext], ProjectRuntimeContext]
type ContextTransition = tuple[ProjectRuntimeContext, ExecutionEvent | None]

_PERSISTED_FIELD_NAMES = tuple(
    item.name
    for item in fields(ProjectRuntimeContext)
    if item.name != "project_owner_agent"
)


@dataclass(slots=True)
class RuntimeContextService:
    database: SQLiteDatabase
    event_bus: EventBus
    contexts: SQLiteProjectRuntimeContextRepository = field(
        default_factory=SQLiteProjectRuntimeContextRepository
    )

    def transition(
        self,
        triage_id: str,
        *,
        reason: RuntimeContextChangeReason,
        mutate: ContextMutation,
    ) -> ProjectRuntimeContext:
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
        mutate: ContextMutation,
    ) -> ContextTransition:
        """Persist one transition inside a caller-owned transaction.

        The returned event must be published only after that transaction commits;
        Timeline handlers use a separate SQLite connection by design.
        """
        current = self.contexts.get(connection, triage_id)
        if current is None:
            raise LookupError(f"Project Runtime Context not found: {triage_id}")
        updated = mutate(current)
        if updated.triage_id != current.triage_id:
            raise ValueError("Runtime Context transition cannot change triage_id")
        changes = _context_changes(current, updated)
        if not changes:
            return updated, None

        self.contexts.update(connection, updated)
        return updated, ExecutionEvent(
            triage_id=triage_id,
            event_type=ExecutionEventType.RUNTIME_CONTEXT_UPDATED,
            payload={
                "reason": reason.value,
                "changes": changes,
            },
        )


def _context_changes(
    current: ProjectRuntimeContext,
    updated: ProjectRuntimeContext,
) -> dict[str, object]:
    changes: dict[str, object] = {}
    for name in _PERSISTED_FIELD_NAMES:
        before = getattr(current, name)
        after = getattr(updated, name)
        if before != after:
            changes[name] = {
                "from": _event_value(before),
                "to": _event_value(after),
            }
    return changes


def _event_value(value: object) -> object:
    return value.isoformat() if isinstance(value, datetime) else value
