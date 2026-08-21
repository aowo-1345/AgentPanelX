"""Private lifecycle for the sole durable Owner work item of one Feature."""

import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from agentplanex.domains import (
    AgentExit,
    AgentExitStatus,
    ExecutionEvent,
    ExecutionEventType,
    OwnerActivation,
    OwnerActivationMode,
    OwnerActivationStatus,
    ProjectOwnerTask,
    ToolExecutionResult,
)
from agentplanex.infrastructure.sqlite.repositories import (
    SQLiteOwnerActivationRepository,
)


class OwnerWorkState(StrEnum):
    """Scheduling state of the Context-owned Owner mailbox."""

    IDLE = "IDLE"
    RUNNABLE = "RUNNABLE"
    WAITING_FOR_CONTROL = "WAITING_FOR_CONTROL"
    RUNNING = "RUNNING"


@dataclass(frozen=True, slots=True)
class ActivationDriveResult:
    """Receipt for one automatic attempt to consume Owner work."""

    activation: OwnerActivation | None
    exit: AgentExit | None


@dataclass(frozen=True, slots=True)
class ToolActivationDriveResult:
    """Receipt for one manually supplied step in the current Owner work."""

    activation: OwnerActivation
    started: bool
    tool_result: ToolExecutionResult | None
    exit: AgentExit | None


@dataclass(frozen=True, slots=True)
class _ActivationClaim:
    activation: OwnerActivation
    started: bool


_FAILED_EXIT_STATUSES = {
    AgentExitStatus.MANUAL_DRIVE_FAILED,
    AgentExitStatus.REPEATED_FORMAT_ERROR,
    AgentExitStatus.STEP_LIMIT_EXCEEDED,
    AgentExitStatus.UNHANDLED_EXCEPTION,
}


@dataclass(slots=True)
class _OwnerActivationLifecycle:
    """Advance Activation facts inside caller-owned SQLite transactions."""

    _records: SQLiteOwnerActivationRepository = field(
        default_factory=SQLiteOwnerActivationRepository
    )

    def submit_input(
        self,
        connection: sqlite3.Connection,
        *,
        triage_id: str,
        owner_input: ProjectOwnerTask,
        message_id: str,
        summary_id: str | None,
    ) -> OwnerActivation:
        unfinished = self._records.get_unfinished(connection, triage_id)
        if unfinished is not None:
            raise ValueError(
                "Project Owner already has unfinished work: "
                f"{unfinished.activation_id} ({unfinished.status.value})"
            )
        activation = OwnerActivation(
            activation_id=uuid4().hex,
            triage_id=triage_id,
            task_type=owner_input.type,
            message_id=message_id,
            summary_id=summary_id,
        )
        self._records.insert(connection, activation)
        return activation

    def work_state(
        self,
        connection: sqlite3.Connection,
        triage_id: str,
    ) -> OwnerWorkState:
        unfinished = self._records.get_unfinished(connection, triage_id)
        if unfinished is None:
            return OwnerWorkState.IDLE
        if unfinished.status is OwnerActivationStatus.PENDING and unfinished.driver_mode is None:
            return OwnerWorkState.RUNNABLE
        if unfinished.status is OwnerActivationStatus.RUNNING:
            return OwnerWorkState.RUNNING
        return OwnerWorkState.WAITING_FOR_CONTROL

    def claim_for_model(
        self,
        connection: sqlite3.Connection,
        triage_id: str,
        *,
        started_at: datetime,
    ) -> OwnerActivation | None:
        unfinished = self._records.get_unfinished(connection, triage_id)
        if unfinished is None:
            return None
        if unfinished.status is OwnerActivationStatus.RUNNING:
            driver = (
                unfinished.driver_mode.value.lower()
                if unfinished.driver_mode is not None
                else "unknown"
            )
            raise ValueError(
                "Project Owner activation is already running through "
                f"{driver}: {unfinished.activation_id}"
            )
        if unfinished.driver_mode is OwnerActivationMode.TOOL:
            raise ValueError(
                "Project Owner activation is already bound to Tool mode: "
                f"{unfinished.activation_id}"
            )
        claimed = self._records.claim_next(
            connection,
            triage_id,
            started_at,
            OwnerActivationMode.MODEL,
        )
        if claimed is None:
            raise RuntimeError("Pending Owner activation could not be claimed")
        return claimed

    def claim_for_tool(
        self,
        connection: sqlite3.Connection,
        triage_id: str,
        *,
        started_at: datetime,
        allow_running: bool = False,
    ) -> _ActivationClaim:
        unfinished = self._records.get_unfinished(connection, triage_id)
        if unfinished is None:
            raise ValueError("Project Owner has no unfinished activation")
        if unfinished.status is OwnerActivationStatus.RUNNING:
            if allow_running and unfinished.driver_mode is OwnerActivationMode.TOOL:
                return _ActivationClaim(activation=unfinished, started=False)
            driver = (
                unfinished.driver_mode.value.lower()
                if unfinished.driver_mode is not None
                else "unknown"
            )
            raise ValueError(
                "Project Owner activation already has a running "
                f"{driver} step: {unfinished.activation_id}"
            )

        started = unfinished.driver_mode is None
        activation = self._records.claim_next(
            connection,
            triage_id,
            started_at,
            OwnerActivationMode.TOOL,
        )
        if activation is None:
            raise RuntimeError("Pending Owner activation could not be claimed")
        return _ActivationClaim(activation=activation, started=started)

    def release_tool(
        self,
        connection: sqlite3.Connection,
        activation: OwnerActivation,
    ) -> OwnerActivation:
        return self._records.release_tool(connection, activation.activation_id)

    def finish(
        self,
        connection: sqlite3.Connection,
        activation: OwnerActivation,
        result: AgentExit,
        *,
        finished_at: datetime,
    ) -> OwnerActivation:
        return (
            self._records.mark_failed(
                connection,
                activation.activation_id,
                finished_at,
                result.content.strip() or result.status.value,
            )
            if result.status in _FAILED_EXIT_STATUSES
            else self._records.mark_completed(
                connection,
                activation.activation_id,
                finished_at,
            )
        )

    def fail_interrupted(
        self,
        connection: sqlite3.Connection,
        triage_id: str,
        *,
        finished_at: datetime,
        failure: str,
    ) -> tuple[OwnerActivation, ...]:
        return self._records.fail_unfinished(
            connection,
            triage_id,
            finished_at=finished_at,
            failure=failure,
        )

    def set_initial_summary(
        self,
        connection: sqlite3.Connection,
        activation_id: str,
        summary_id: str,
    ) -> None:
        self._records.set_initial_summary(connection, activation_id, summary_id)

    @staticmethod
    def entered_event(activation: OwnerActivation) -> ExecutionEvent:
        if activation.driver_mode is None:
            raise RuntimeError("Claimed Owner activation has no driver mode")
        return ExecutionEvent(
            triage_id=activation.triage_id,
            event_type=ExecutionEventType.REACT_LOOP_ENTERED,
            react_loop_id=activation.activation_id,
            payload={
                "task_type": activation.task_type.value,
                "driver_mode": activation.driver_mode.value,
            },
        )

    @staticmethod
    def exited_event(
        activation: OwnerActivation,
        result: AgentExit,
        *,
        interrupted: bool = False,
    ) -> ExecutionEvent:
        if activation.driver_mode is None:
            raise RuntimeError("Finalized Owner activation has no driver mode")
        payload: dict[str, object] = {
            "agent_exit_status": result.status.value,
            "driver_mode": activation.driver_mode.value,
        }
        if interrupted:
            payload["interrupted"] = True
        return ExecutionEvent(
            triage_id=activation.triage_id,
            event_type=ExecutionEventType.REACT_LOOP_EXITED,
            react_loop_id=activation.activation_id,
            payload=payload,
        )

    @staticmethod
    def interrupted_event(activation: OwnerActivation) -> ExecutionEvent:
        if activation.status is not OwnerActivationStatus.FAILED:
            raise ValueError("Interrupted event requires a failed Activation")
        if activation.driver_mode is None or activation.failure is None:
            raise ValueError("Failed Activation is missing its terminal facts")
        return ExecutionEvent(
            triage_id=activation.triage_id,
            event_type=ExecutionEventType.OWNER_ACTIVATION_FAILED,
            react_loop_id=(activation.activation_id if activation.started_at is not None else None),
            payload={
                "activation_id": activation.activation_id,
                "task_type": activation.task_type.value,
                "driver_mode": activation.driver_mode.value,
                "failure": activation.failure,
                "interrupted": True,
                "started": activation.started_at is not None,
            },
        )


def _unhandled_exit(error: Exception) -> AgentExit:
    return AgentExit(
        status=AgentExitStatus.UNHANDLED_EXCEPTION,
        content=f"{type(error).__name__}: {error}",
    )


def _manual_failure(reason: str) -> AgentExit:
    failure = reason.strip()
    if not failure:
        raise ValueError("Project Owner failure reason must not be empty")
    return AgentExit(
        status=AgentExitStatus.MANUAL_DRIVE_FAILED,
        content=failure,
    )


def _now() -> datetime:
    return datetime.now(UTC)
