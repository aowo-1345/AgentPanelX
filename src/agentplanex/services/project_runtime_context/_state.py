"""Private State transition rules shared during the staged Context migration."""

import sqlite3
from collections.abc import Callable
from dataclasses import fields
from datetime import datetime

from agentplanex.domains import (
    ExecutionEvent,
    ExecutionEventType,
    ProjectRuntimeState,
    RuntimeContextChangeReason,
)
from agentplanex.infrastructure.sqlite.repositories import (
    SQLiteProjectRuntimeStateRepository,
)

type StateMutation = Callable[[ProjectRuntimeState], ProjectRuntimeState]
type StateTransition = tuple[ProjectRuntimeState, ExecutionEvent | None]


def apply_state_transition(
    connection: sqlite3.Connection,
    states: SQLiteProjectRuntimeStateRepository,
    current: ProjectRuntimeState,
    *,
    reason: RuntimeContextChangeReason,
    mutate: StateMutation,
) -> StateTransition:
    """Persist one validated State change and return its uncommitted event."""
    updated = mutate(current)
    if updated.triage_id != current.triage_id:
        raise ValueError("Runtime State transition cannot change Feature identity")
    changes = _state_changes(current, updated)
    if not changes:
        return updated, None
    states.update(connection, updated)
    return updated, ExecutionEvent(
        triage_id=updated.triage_id,
        event_type=ExecutionEventType.RUNTIME_CONTEXT_UPDATED,
        payload={"reason": reason.value, "changes": changes},
    )


_STATE_FIELD_NAMES = tuple(item.name for item in fields(ProjectRuntimeState))


def _state_changes(
    current: ProjectRuntimeState,
    updated: ProjectRuntimeState,
) -> dict[str, object]:
    changes: dict[str, object] = {}
    for name in _STATE_FIELD_NAMES:
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
