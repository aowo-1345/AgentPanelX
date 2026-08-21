"""One-step, manually driven execution of durable delivery StageRuns."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import uuid4

from agentplanex.domains import (
    ExecutionEvent,
    ExecutionEventType,
    Milestone,
    OwnerActivation,
    StageRun,
)
from agentplanex.infrastructure.git_repository import GitRepository, GitRepositoryError
from agentplanex.services.delivery import (
    DeliveryError,
    DeliveryService,
    StageCompletion,
    delivery_candidate_ref,
    delivery_run_ref,
)
from agentplanex.services.stage_executor import StageExecutionRequest, StageExecutor


@dataclass(frozen=True, slots=True)
class DeliveryDriveResult:
    """The visible outcome of one explicit delivery-driver command."""

    outcome: Literal["idle", "stage_succeeded", "candidate_ready", "stage_failed"]
    stage_run: StageRun | None
    context_status: str | None
    candidate_commit_sha: str | None
    activation: OwnerActivation | None


@dataclass(slots=True)
class DeliveryRunner:
    """Run at most one queued Stage outside SQLite transactions."""

    delivery: DeliveryService
    executor: StageExecutor
    git: GitRepository
    lease_duration: timedelta = timedelta(minutes=30)

    def __post_init__(self) -> None:
        if self.lease_duration <= timedelta(0):
            raise ValueError("StageRun lease duration must be positive")

    def drive_once(self) -> DeliveryDriveResult:
        """Claim, execute, and finalize exactly one StageRun when one is queued."""
        active = self.delivery.active_stage_run()
        if active is None:
            return DeliveryDriveResult(
                outcome="idle",
                stage_run=None,
                context_status=None,
                candidate_commit_sha=None,
                activation=None,
            )
        now = datetime.now(UTC)
        if active.status.value == "RUNNING":
            if active.lease_expires_at is None or active.lease_expires_at > now:
                raise DeliveryError(
                    "StageRun is already running; wait for its lease or terminal result"
                )
            completion = self.delivery.fail_stage(
                active.stage_run_id,
                failure="Stage execution lease expired before a terminal result",
                finished_at=now,
            )
            self._remove_worktree(active.run_id)
            return self._failed_result(completion)

        claim = self.delivery.claim_next_stage(
            started_at=now,
            lease_expires_at=now + self.lease_duration,
        )
        triage_id = claim.state.triage_id
        invocation_id = uuid4().hex
        invocation_started = False
        try:
            worktree = self.git.prepare_delivery_worktree(
                claim.stage_run.run_id,
                claim.stage_run.input_commit_sha,
            )
            delivery_document = _delivery_document_path(
                worktree,
                claim.stage_run.run_id,
                claim.stage.key,
            )
            self.delivery.event_bus.publish(
                ExecutionEvent(
                    triage_id=triage_id,
                    event_type=ExecutionEventType.AGENT_INVOCATION_STARTED,
                    payload={
                        "invocation_id": invocation_id,
                        "operation": "stage_executor",
                        "stage_run_id": claim.stage_run.stage_run_id,
                        "run_id": claim.stage_run.run_id,
                        "input_commit_sha": claim.stage_run.input_commit_sha,
                    },
                )
            )
            invocation_started = True
            self.executor.execute(
                StageExecutionRequest(
                    stage_run=claim.stage_run,
                    milestone=claim.milestone,
                    stage=claim.stage,
                    worktree=worktree,
                    delivery_document=delivery_document,
                )
            )
            worktree_git = GitRepository(worktree)
            self._validate_stage_output(
                worktree_git,
                claim.stage_run,
                delivery_document,
            )
            output_commit_sha = worktree_git.commit_all(
                message=(
                    "stage: "
                    f"{claim.milestone.key}/{claim.stage.key} "
                    f"({claim.stage_run.run_id})"
                )
            )
            self.git.update_ref(delivery_run_ref(claim.stage_run.run_id), output_commit_sha)
            if _is_final_stage(claim.milestone, claim.stage.key):
                self.git.update_ref(
                    delivery_candidate_ref(claim.stage_run.run_id),
                    output_commit_sha,
                )
            completion = self.delivery.succeed_stage(
                claim.stage_run.stage_run_id,
                output_commit_sha=output_commit_sha,
                finished_at=datetime.now(UTC),
            )
            self.delivery.event_bus.publish(
                ExecutionEvent(
                    triage_id=triage_id,
                    event_type=ExecutionEventType.AGENT_INVOCATION_COMPLETED,
                    payload={
                        "invocation_id": invocation_id,
                        "operation": "stage_executor",
                        "stage_run_id": claim.stage_run.stage_run_id,
                        "run_id": claim.stage_run.run_id,
                        "output_commit_sha": output_commit_sha,
                    },
                )
            )
        except Exception as error:
            if invocation_started:
                self.delivery.event_bus.publish(
                    ExecutionEvent(
                        triage_id=triage_id,
                        event_type=ExecutionEventType.AGENT_INVOCATION_FAILED,
                        payload={
                            "invocation_id": invocation_id,
                            "operation": "stage_executor",
                            "stage_run_id": claim.stage_run.stage_run_id,
                            "run_id": claim.stage_run.run_id,
                            "failure_type": type(error).__name__,
                        },
                    )
                )
            completion = self.delivery.fail_stage(
                claim.stage_run.stage_run_id,
                failure=_failure_message(error),
                finished_at=datetime.now(UTC),
            )
            self._remove_worktree(claim.stage_run.run_id)
            return self._failed_result(completion)

        if completion.candidate_commit_sha is not None:
            self._remove_worktree(claim.stage_run.run_id)
            return DeliveryDriveResult(
                outcome="candidate_ready",
                stage_run=completion.stage_run,
                context_status=completion.state.status,
                candidate_commit_sha=completion.candidate_commit_sha,
                activation=completion.activation,
            )
        return DeliveryDriveResult(
            outcome="stage_succeeded",
            stage_run=completion.stage_run,
            context_status=completion.state.status,
            candidate_commit_sha=None,
            activation=None,
        )

    @staticmethod
    def _validate_stage_output(
        worktree_git: GitRepository,
        stage_run: StageRun,
        delivery_document: Path,
    ) -> None:
        if worktree_git.head_sha() != stage_run.input_commit_sha:
            raise DeliveryError("Stage Executor changed the delivery worktree HEAD")
        try:
            relative_document = str(
                delivery_document.resolve().relative_to(worktree_git.project_path.resolve())
            )
        except ValueError as error:
            raise DeliveryError("Stage delivery document escaped its worktree") from error
        if not delivery_document.is_file() or delivery_document.is_symlink():
            raise DeliveryError("Stage did not create its required delivery document")
        try:
            document = delivery_document.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise DeliveryError("Stage delivery document cannot be read") from error
        if not document.strip():
            raise DeliveryError("Stage delivery document must not be empty")
        changed = worktree_git.changed_paths()
        if relative_document not in changed:
            raise DeliveryError("Stage did not modify its required delivery document")
        if not any(path != relative_document for path in changed):
            raise DeliveryError(
                "Stage must modify at least one project file besides its delivery document"
            )

    def _remove_worktree(self, run_id: str) -> None:
        try:
            self.git.remove_delivery_worktree(run_id)
        except GitRepositoryError:
            # Terminal facts and refs remain authoritative even when cleanup is delayed.
            return

    @staticmethod
    def _failed_result(completion: StageCompletion) -> DeliveryDriveResult:
        return DeliveryDriveResult(
            outcome="stage_failed",
            stage_run=completion.stage_run,
            context_status=completion.state.status,
            candidate_commit_sha=None,
            activation=completion.activation,
        )


def _delivery_document_path(worktree: Path, run_id: str, stage_key: str) -> Path:
    return (
        worktree
        / "docs"
        / "agentplanex"
        / "deliveries"
        / run_id
        / f"{stage_key}.md"
    )


def _is_final_stage(milestone: Milestone, stage_key: str) -> bool:
    return milestone.stages[-1].key == stage_key


def _failure_message(error: Exception) -> str:
    detail = " ".join(str(error).split())
    return f"{type(error).__name__}: {detail}"[:2_000]
