"""Claim and consume the durable Project Owner activation mailbox."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from agentplanex.domains import (
    AgentExit,
    AgentExitStatus,
    OwnerActivation,
)
from agentplanex.infrastructure.sqlite import SQLiteDatabase
from agentplanex.infrastructure.sqlite.repositories import (
    SQLiteOwnerActivationRepository,
)

type OwnerActivationRunner = Callable[[OwnerActivation], AgentExit]

_FAILED_EXIT_STATUSES = {
    AgentExitStatus.REPEATED_FORMAT_ERROR,
    AgentExitStatus.STEP_LIMIT_EXCEEDED,
    AgentExitStatus.UNHANDLED_EXCEPTION,
}


@dataclass(frozen=True, slots=True)
class ActivationDriveResult:
    """The outcome of attempting to consume one mailbox item."""

    activation: OwnerActivation | None
    exit: AgentExit | None


@dataclass(slots=True)
class OwnerActivationDriver:
    """Serialize Owner loops by claiming and finalizing one activation at a time."""

    database: SQLiteDatabase
    run_owner: OwnerActivationRunner
    activations: SQLiteOwnerActivationRepository = field(
        default_factory=SQLiteOwnerActivationRepository
    )

    def drive_next(self, triage_id: str) -> ActivationDriveResult:
        with self.database.transaction() as connection:
            activation = self.activations.claim_next(
                connection,
                triage_id,
                datetime.now(UTC),
            )
        if activation is None:
            return ActivationDriveResult(activation=None, exit=None)

        try:
            result = self.run_owner(activation)
        except Exception as error:
            result = AgentExit(
                status=AgentExitStatus.UNHANDLED_EXCEPTION,
                content=f"{type(error).__name__}: {error}",
            )

        with self.database.transaction() as connection:
            completed = (
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
        return ActivationDriveResult(activation=completed, exit=result)
