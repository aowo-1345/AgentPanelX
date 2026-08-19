"""Claim and consume the durable Project Owner activation mailbox."""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from agentplanex.domains import (
    AgentExit,
    AgentExitStatus,
    OwnerActivation,
    OwnerActivationMode,
    OwnerActivationStatus,
)
from agentplanex.infrastructure.sqlite import SQLiteDatabase
from agentplanex.infrastructure.sqlite.repositories import (
    SQLiteOwnerActivationRepository,
)

type OwnerActivationRunner = Callable[[OwnerActivation], AgentExit]

_FAILED_EXIT_STATUSES = {
    AgentExitStatus.MANUAL_DRIVE_FAILED,
    AgentExitStatus.REPEATED_FORMAT_ERROR,
    AgentExitStatus.STEP_LIMIT_EXCEEDED,
    AgentExitStatus.UNHANDLED_EXCEPTION,
}


@dataclass(frozen=True, slots=True)
class ActivationDriveResult:
    """The outcome of attempting to consume one mailbox item."""

    activation: OwnerActivation | None
    exit: AgentExit | None


@dataclass(frozen=True, slots=True)
class ActivationClaim:
    """A Tool driver claim, including whether this call started the loop."""

    activation: OwnerActivation
    started: bool


@dataclass(slots=True)
class OwnerActivationDriver:
    """Serialize Owner loops by claiming and finalizing one activation at a time."""

    database: SQLiteDatabase
    run_owner: OwnerActivationRunner
    activations: SQLiteOwnerActivationRepository = field(
        default_factory=SQLiteOwnerActivationRepository
    )

    def drive_next(self, triage_id: str) -> ActivationDriveResult:
        activation = self._claim_for_model(triage_id)
        if activation is None:
            return ActivationDriveResult(activation=None, exit=None)

        try:
            result = self.run_owner(activation)
        except Exception as error:
            result = AgentExit(
                status=AgentExitStatus.UNHANDLED_EXCEPTION,
                content=f"{type(error).__name__}: {error}",
            )

        return ActivationDriveResult(
            activation=self.finish(activation, result),
            exit=result,
        )

    def unfinished(self, triage_id: str) -> OwnerActivation | None:
        """Read the sole unfinished Activation through its owning service."""
        with self.database.connection() as connection:
            return self.activations.get_unfinished(connection, triage_id)

    def fail_interrupted(
        self,
        connection: sqlite3.Connection,
        triage_id: str,
        *,
        finished_at: datetime,
        failure: str,
    ) -> tuple[OwnerActivation, ...]:
        """Terminalize unfinished Activations inside the caller's transaction."""
        return self.activations.fail_unfinished(
            connection,
            triage_id,
            finished_at=finished_at,
            failure=failure,
        )

    def claim_for_tool(self, triage_id: str) -> ActivationClaim:
        """Atomically claim the next step of a Tool-driven Owner loop."""

        with self.database.transaction() as connection:
            unfinished = self.activations.get_unfinished(connection, triage_id)
            if unfinished is None:
                raise ValueError("Project Owner has no unfinished activation")
            if unfinished.status is OwnerActivationStatus.RUNNING:
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
            activation = self.activations.claim_next(
                connection,
                triage_id,
                datetime.now(UTC),
                OwnerActivationMode.TOOL,
            )
            if activation is None:
                raise RuntimeError("Pending Owner activation could not be claimed")
            return ActivationClaim(activation=activation, started=started)

    def claim_for_tool_failure(self, triage_id: str) -> ActivationClaim:
        """Claim a waiting Tool loop or select its stuck running step for failure."""

        with self.database.transaction() as connection:
            unfinished = self.activations.get_unfinished(connection, triage_id)
            if unfinished is None:
                raise ValueError("Project Owner has no unfinished activation")
            if unfinished.status is OwnerActivationStatus.RUNNING:
                if unfinished.driver_mode is not OwnerActivationMode.TOOL:
                    raise ValueError(
                        "A model-driven Owner activation cannot be failed by Tool mode: "
                        f"{unfinished.activation_id}"
                    )
                return ActivationClaim(activation=unfinished, started=False)

            started = unfinished.driver_mode is None
            activation = self.activations.claim_next(
                connection,
                triage_id,
                datetime.now(UTC),
                OwnerActivationMode.TOOL,
            )
            if activation is None:
                raise RuntimeError("Pending Owner activation could not be claimed")
            return ActivationClaim(activation=activation, started=started)

    def release_tool(self, activation: OwnerActivation) -> OwnerActivation:
        """Return a non-terminal Tool loop to its durable waiting state."""

        with self.database.transaction() as connection:
            return self.activations.release_tool(
                connection,
                activation.activation_id,
            )

    def finish(
        self,
        activation: OwnerActivation,
        result: AgentExit,
    ) -> OwnerActivation:
        """Finalize one claimed activation from either supported driver."""

        with self.database.transaction() as connection:
            return (
                self.activations.mark_failed(
                    connection,
                    activation.activation_id,
                    datetime.now(UTC),
                    result.content.strip() or result.status.value,
                )
                if result.status in _FAILED_EXIT_STATUSES
                else self.activations.mark_completed(
                    connection,
                    activation.activation_id,
                    datetime.now(UTC),
                )
            )

    def _claim_for_model(self, triage_id: str) -> OwnerActivation | None:
        with self.database.transaction() as connection:
            unfinished = self.activations.get_unfinished(connection, triage_id)
            if (
                unfinished is not None
                and unfinished.status is OwnerActivationStatus.RUNNING
            ):
                driver = (
                    unfinished.driver_mode.value.lower()
                    if unfinished.driver_mode is not None
                    else "unknown"
                )
                raise ValueError(
                    "Project Owner activation is already running through "
                    f"{driver}: "
                    f"{unfinished.activation_id}"
                )
            if (
                unfinished is not None
                and unfinished.driver_mode is OwnerActivationMode.TOOL
            ):
                raise ValueError(
                    "Project Owner activation is already bound to Tool mode: "
                    f"{unfinished.activation_id}"
                )
            return self.activations.claim_next(
                connection,
                triage_id,
                datetime.now(UTC),
                OwnerActivationMode.MODEL,
            )
