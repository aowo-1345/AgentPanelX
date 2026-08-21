"""Milestone publication and rolling-delivery state transitions."""

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from agentplanex.domains import (
    ArtifactDescriptor,
    ExecutionEvent,
    ExecutionEventType,
    Milestone,
    MilestoneSnapshot,
    MilestoneState,
    OwnerActivation,
    ProjectOwnerTask,
    ProjectOwnerTaskType,
    ProjectRuntimeState,
    RuntimeContextChangeReason,
    Stage,
    StageRun,
    StageRunStatus,
    milestone_view_digest,
)
from agentplanex.infrastructure.git_repository import GitRepository, GitRepositoryError
from agentplanex.infrastructure.sqlite.repositories import (
    SQLiteMilestoneSnapshotRepository,
    SQLiteStageRunRepository,
)
from agentplanex.services.event_bus import EventBus
from agentplanex.services.planning import SPEC_DOCUMENT_NAMES
from agentplanex.services.project_runtime_context import ProjectRuntimeContext


class DeliveryError(ValueError):
    """An expected Delivery Contract error that the Owner can correct."""


@dataclass(frozen=True, slots=True)
class MilestoneReviewRequest:
    """The exact complete Milestone View supplied to a protected review."""

    triage_id: str
    plan_commit_sha: str
    milestones: tuple[Milestone, ...]
    subject_digest: str


@dataclass(frozen=True, slots=True)
class MilestoneReviewResult:
    """Validated result required by the Milestone publication Hard Gate."""

    subject_digest: str
    decision: str
    summary: str
    required_changes: tuple[str, ...]
    audit_artifact: ArtifactDescriptor


type MilestoneHardGate = Callable[[MilestoneReviewRequest], MilestoneReviewResult]
def missing_milestone_hard_gate(_request: MilestoneReviewRequest) -> MilestoneReviewResult:
    """Fail closed when no Milestone Gate is bound at composition time."""
    raise DeliveryError("Milestone Hard Gate is not configured")


@dataclass(frozen=True, slots=True)
class MilestonesUpdated:
    """Observable result of publishing one complete Milestone View."""

    state: ProjectRuntimeState
    snapshot: MilestoneSnapshot | None
    accepted: bool
    subject_digest: str
    review: MilestoneReviewResult | None


@dataclass(frozen=True, slots=True)
class FirstRunApprovalRequested:
    """The first Run is ready but still requires an explicit user Start."""

    state: ProjectRuntimeState
    snapshot: MilestoneSnapshot
    milestone: Milestone


@dataclass(frozen=True, slots=True)
class MilestoneRunQueued:
    """A fixed first Stage has been durably queued for one Milestone Run."""

    state: ProjectRuntimeState
    snapshot: MilestoneSnapshot
    milestone: Milestone
    stage: Stage
    stage_run: StageRun
    first_run: bool


@dataclass(frozen=True, slots=True)
class StageClaim:
    """A Driver-owned lease over the sole queued StageRun."""

    state: ProjectRuntimeState
    snapshot: MilestoneSnapshot
    milestone: Milestone
    stage: Stage
    stage_run: StageRun


@dataclass(frozen=True, slots=True)
class StageCompletion:
    """One terminal Stage fact and either its successor or Candidate result."""

    state: ProjectRuntimeState
    stage_run: StageRun
    next_stage_run: StageRun | None
    candidate_commit_sha: str | None
    activation: OwnerActivation | None


@dataclass(frozen=True, slots=True)
class CandidateDecision:
    """The controlled outcome of accepting or rejecting one Candidate."""

    state: ProjectRuntimeState
    decision: Literal["accept", "reject"]
    milestone_key: str
    candidate_commit_sha: str
    snapshot: MilestoneSnapshot | None
    next_milestone_key: str | None
    completed: bool


@dataclass(slots=True)
class DeliveryService:
    """Own Snapshot publication now and the delivery state machine incrementally."""

    project_path: Path
    context: ProjectRuntimeContext
    git: GitRepository
    event_bus: EventBus = field(default_factory=EventBus)
    snapshots: SQLiteMilestoneSnapshotRepository = field(
        default_factory=SQLiteMilestoneSnapshotRepository
    )
    stage_runs: SQLiteStageRunRepository = field(
        default_factory=SQLiteStageRunRepository
    )
    review_milestones: MilestoneHardGate = missing_milestone_hard_gate

    def update_milestones(
        self,
        *,
        reason: str,
        milestones: tuple[Milestone, ...],
    ) -> MilestonesUpdated:
        """Publish a complete View after checks and the IN_PROGRESS Hard Gate."""
        normalized_reason = " ".join(reason.split())
        if not normalized_reason:
            raise DeliveryError("Milestone update reason must not be empty")
        current = self.context.state()
        previous = self._assert_publishable(current, milestones)
        self._assert_approved_specs(current)
        plan_commit_sha = current.current_plan_commit_sha
        if plan_commit_sha is None:
            raise DeliveryError("Milestone publication requires an approved Plan")
        subject_digest = milestone_view_digest(milestones)
        review = (
            self._run_milestone_hard_gate(
                current,
                plan_commit_sha,
                milestones,
                subject_digest,
            )
            if current.status == "IN_PROGRESS"
            else None
        )
        current = self.context.state()
        previous = self._assert_publishable(current, milestones)
        self._assert_approved_specs(current)
        if review is not None and review.decision == "revise":
            blocked = self.context.transition(
                reason=RuntimeContextChangeReason.MILESTONE_HARD_GATE_REJECTED,
                mutate=self._block_after_milestone_hard_gate_rejection,
            )
            return MilestonesUpdated(
                state=blocked,
                snapshot=None,
                accepted=False,
                subject_digest=subject_digest,
                review=review,
            )

        with self.context.transaction() as transaction:
            latest = transaction.state()
            snapshot = MilestoneSnapshot(
                snapshot_id=uuid4().hex,
                triage_id=latest.triage_id,
                previous_snapshot_id=(
                    previous.snapshot_id if previous is not None else None
                ),
                plan_commit_sha=latest.current_plan_commit_sha or "",
                milestones=milestones,
                reason=normalized_reason,
                message_id=transaction.owner_message_id(),
                created_at=datetime.now(UTC),
            )
            self.snapshots.insert(transaction.connection, snapshot)
            updated = transaction.transition(
                reason=RuntimeContextChangeReason.MILESTONES_UPDATED,
                mutate=lambda latest: self._publish_snapshot(
                    transaction.connection,
                    latest,
                    snapshot,
                ),
            )
        self.event_bus.publish(
            ExecutionEvent(
                triage_id=updated.triage_id,
                event_type=ExecutionEventType.MILESTONES_UPDATED,
                payload={
                    "snapshot_id": snapshot.snapshot_id,
                    "previous_snapshot_id": snapshot.previous_snapshot_id,
                    "plan_commit_sha": snapshot.plan_commit_sha,
                    "milestone_count": len(snapshot.milestones),
                    "subject_digest": subject_digest,
                    "hard_gate_invoked": review is not None,
                },
            )
        )
        return MilestonesUpdated(
            state=updated,
            snapshot=snapshot,
            accepted=True,
            subject_digest=subject_digest,
            review=review,
        )

    def request_next_milestone(self) -> FirstRunApprovalRequested | MilestoneRunQueued:
        """Request the first Start or queue the next pending Milestone Run."""
        current = self.context.state()
        snapshot = self._snapshot_for_context(current)
        milestone = self._first_pending(snapshot)
        self._assert_approved_specs(current)
        if current.rolling_started_at is None:
            if current.status != "TODO" or current.pending_action is not None:
                raise DeliveryError(
                    "First Run can only be requested from TODO with no pending action"
                )
            if current.current_run_id is not None:
                raise DeliveryError("First Run already has an active Milestone Run")
            updated = self.context.transition(
                reason=RuntimeContextChangeReason.FIRST_RUN_APPROVAL_REQUESTED,
                mutate=lambda latest: self._request_first_run(latest, snapshot),
            )
            self.event_bus.publish(
                ExecutionEvent(
                    triage_id=updated.triage_id,
                    event_type=ExecutionEventType.FIRST_RUN_APPROVAL_REQUESTED,
                    payload={
                        "snapshot_id": snapshot.snapshot_id,
                        "milestone_key": milestone.key,
                    },
                )
            )
            return FirstRunApprovalRequested(
                state=updated,
                snapshot=snapshot,
                milestone=milestone,
            )
        if current.status == "BLOCKED":
            self._assert_retryable_blocked(current)
        return self._queue_next_run(current, snapshot, milestone, first_run=False)

    def start_first_run(self) -> MilestoneRunQueued:
        """Apply the user's one-time explicit Start and queue the first Stage."""
        current = self.context.state()
        snapshot = self._snapshot_for_context(current)
        milestone = self._first_pending(snapshot)
        if (
            current.status != "READY"
            or current.pending_action != "FIRST_RUN_APPROVAL"
            or current.rolling_started_at is not None
        ):
            raise DeliveryError("Project is not waiting for its first Run approval")
        return self._queue_next_run(current, snapshot, milestone, first_run=True)

    def active_stage_run(self) -> StageRun | None:
        """Read the sole active StageRun for the Driver without changing it."""
        with self.context.transaction() as transaction:
            state = transaction.state()
            return self.stage_runs.get_active(
                transaction.connection,
                state.triage_id,
            )

    def fail_interrupted_stage_runs(
        self,
        connection: sqlite3.Connection,
        triage_id: str,
        *,
        finished_at: datetime,
        failure: str,
    ) -> tuple[StageRun, ...]:
        """Terminalize active StageRuns inside the caller's transaction."""
        return self.stage_runs.fail_active(
            connection,
            triage_id,
            finished_at=finished_at,
            failure=failure,
        )

    def claim_next_stage(
        self,
        *,
        started_at: datetime,
        lease_expires_at: datetime,
    ) -> StageClaim:
        """Atomically claim the next queued Stage before leaving the transaction."""
        with self.context.transaction() as transaction:
            current = transaction.state()
            active = self.stage_runs.get_active(
                transaction.connection,
                current.triage_id,
            )
            if active is None:
                raise DeliveryError("Project has no queued StageRun")
            if active.status is not StageRunStatus.QUEUED:
                raise DeliveryError("Project already has a running StageRun")
            snapshot, milestone, stage = self._stage_contract(
                transaction.connection,
                current,
                active,
            )
            claimed = self.stage_runs.claim_next(
                transaction.connection,
                current.triage_id,
                started_at=started_at,
                lease_expires_at=lease_expires_at,
            )
            if claimed is None:
                raise DeliveryError("StageRun could not be claimed")
        self.event_bus.publish(
            ExecutionEvent(
                triage_id=current.triage_id,
                event_type=ExecutionEventType.STAGE_RUN_STARTED,
                payload={
                    "stage_run_id": claimed.stage_run_id,
                    "run_id": claimed.run_id,
                    "snapshot_id": claimed.snapshot_id,
                    "milestone_key": claimed.milestone_key,
                    "stage_key": claimed.stage_key,
                    "input_commit_sha": claimed.input_commit_sha,
                    "lease_expires_at": lease_expires_at.isoformat(),
                },
            )
        )
        return StageClaim(
            state=current,
            snapshot=snapshot,
            milestone=milestone,
            stage=stage,
            stage_run=claimed,
        )

    def succeed_stage(
        self,
        stage_run_id: str,
        *,
        output_commit_sha: str,
        finished_at: datetime,
    ) -> StageCompletion:
        """Record one committed Stage and queue only its ordered successor."""
        with self.context.transaction() as transaction:
            running = self.stage_runs.get(transaction.connection, stage_run_id)
            if running is None:
                raise LookupError(f"StageRun not found: {stage_run_id}")
            current = transaction.state()
            if current.triage_id != running.triage_id:
                raise DeliveryError("StageRun does not belong to this Project Runtime")
            _snapshot, milestone, stage = self._stage_contract(
                transaction.connection,
                current,
                running,
            )
            if running.status is not StageRunStatus.RUNNING:
                raise DeliveryError("Only a running StageRun can succeed")
            succeeded = self.stage_runs.mark_succeeded(
                transaction.connection,
                stage_run_id,
                output_commit_sha=output_commit_sha,
                finished_at=finished_at,
            )
            stage_index = _stage_index(milestone, stage.key)
            next_stage_run: StageRun | None = None
            candidate_commit_sha: str | None = None
            activation: OwnerActivation | None = None
            if stage_index + 1 < len(milestone.stages):
                next_stage = milestone.stages[stage_index + 1]
                next_stage_run = StageRun(
                    stage_run_id=uuid4().hex,
                    triage_id=current.triage_id,
                    run_id=running.run_id,
                    snapshot_id=running.snapshot_id,
                    milestone_key=running.milestone_key,
                    stage_key=next_stage.key,
                    status=StageRunStatus.QUEUED,
                    input_commit_sha=output_commit_sha,
                    output_commit_sha=None,
                    failure=None,
                    created_at=finished_at,
                )
                self.stage_runs.insert(transaction.connection, next_stage_run)
                updated = transaction.transition(
                    reason=RuntimeContextChangeReason.STAGE_RUN_SUCCEEDED,
                    mutate=lambda latest: self._advance_stage(
                        latest,
                        running,
                        next_stage,
                    ),
                )
            else:
                candidate_commit_sha = output_commit_sha
                updated = transaction.transition(
                    reason=RuntimeContextChangeReason.CANDIDATE_READY,
                    mutate=lambda latest: self._candidate_ready(
                        latest,
                        running,
                        output_commit_sha,
                    ),
                )
                activation = transaction.submit_owner_input(
                    ProjectOwnerTask(
                        type=ProjectOwnerTaskType.EXECUTION_RESULT,
                        content=_candidate_ready_message(
                            updated,
                            milestone,
                            running,
                            output_commit_sha,
                        ),
                    )
                )

        self.event_bus.publish(
            ExecutionEvent(
                triage_id=updated.triage_id,
                event_type=ExecutionEventType.STAGE_RUN_SUCCEEDED,
                payload={
                    "stage_run_id": succeeded.stage_run_id,
                    "run_id": succeeded.run_id,
                    "milestone_key": succeeded.milestone_key,
                    "stage_key": succeeded.stage_key,
                    "input_commit_sha": succeeded.input_commit_sha,
                    "output_commit_sha": succeeded.output_commit_sha,
                    "next_stage_run_id": (
                        next_stage_run.stage_run_id if next_stage_run is not None else None
                    ),
                },
            )
        )
        if candidate_commit_sha is not None:
            self.event_bus.publish(
                ExecutionEvent(
                    triage_id=updated.triage_id,
                    event_type=ExecutionEventType.CANDIDATE_READY,
                    payload={
                        "run_id": succeeded.run_id,
                        "milestone_key": succeeded.milestone_key,
                        "candidate_commit_sha": candidate_commit_sha,
                    },
                )
            )
        return StageCompletion(
            state=updated,
            stage_run=succeeded,
            next_stage_run=next_stage_run,
            candidate_commit_sha=candidate_commit_sha,
            activation=activation,
        )

    def fail_stage(
        self,
        stage_run_id: str,
        *,
        failure: str,
        finished_at: datetime,
    ) -> StageCompletion:
        """Record a terminal Stage failure and block delivery."""
        normalized_failure = " ".join(failure.split())
        if not normalized_failure:
            raise ValueError("Stage failure must not be empty")
        with self.context.transaction() as transaction:
            running = self.stage_runs.get(transaction.connection, stage_run_id)
            if running is None:
                raise LookupError(f"StageRun not found: {stage_run_id}")
            current = transaction.state()
            if current.triage_id != running.triage_id:
                raise DeliveryError("StageRun does not belong to this Project Runtime")
            self._stage_contract(transaction.connection, current, running)
            if running.status is not StageRunStatus.RUNNING:
                raise DeliveryError("Only a running StageRun can fail")
            failed = self.stage_runs.mark_failed(
                transaction.connection,
                stage_run_id,
                failure=normalized_failure,
                finished_at=finished_at,
            )
            updated = transaction.transition(
                reason=RuntimeContextChangeReason.STAGE_RUN_FAILED,
                mutate=lambda latest: self._stage_failed(latest, running),
            )
        self.event_bus.publish(
            ExecutionEvent(
                triage_id=updated.triage_id,
                event_type=ExecutionEventType.STAGE_RUN_FAILED,
                payload={
                    "stage_run_id": failed.stage_run_id,
                    "run_id": failed.run_id,
                    "milestone_key": failed.milestone_key,
                    "stage_key": failed.stage_key,
                    "input_commit_sha": failed.input_commit_sha,
                },
            )
        )
        return StageCompletion(
            state=updated,
            stage_run=failed,
            next_stage_run=None,
            candidate_commit_sha=None,
            activation=None,
        )

    def decide_milestone_candidate(
        self,
        *,
        decision: Literal["accept", "reject"],
        reason: str,
    ) -> CandidateDecision:
        """Accept or reject the fixed Candidate without letting the Owner mutate Git."""
        normalized_reason = " ".join(reason.split())
        if not normalized_reason:
            raise DeliveryError("Candidate decision reason must not be empty")
        current = self.context.state()
        snapshot, milestone, candidate_commit_sha = self._candidate_contract(current)
        self._assert_candidate_ref(current, candidate_commit_sha)
        successor: MilestoneSnapshot | None = None
        completed = False
        integrated_commit_sha = candidate_commit_sha
        if decision == "accept":
            self._assert_candidate_preserves_specs(current, candidate_commit_sha)
            git = self.git
            try:
                if current.git_branch is None or current.git_main_version is None:
                    raise DeliveryError("Candidate has no fixed target branch and commit")
                integrated_commit_sha = git.integrate_fast_forward(
                    candidate_commit_sha,
                    expected_branch=current.git_branch,
                    expected_head=current.git_main_version,
                )
            except GitRepositoryError as error:
                raise DeliveryError(str(error)) from error

        with self.context.transaction() as transaction:
            latest = transaction.state()
            latest_snapshot, latest_milestone, latest_candidate = self._candidate_contract(
                latest,
                connection=transaction.connection,
            )
            if (
                latest_snapshot.snapshot_id != snapshot.snapshot_id
                or latest_milestone.key != milestone.key
                or latest_candidate != candidate_commit_sha
            ):
                raise DeliveryError("Candidate changed while applying its decision")
            if decision == "accept":
                successor = latest_snapshot.with_completed_milestone(
                    latest_milestone.key,
                    snapshot_id=uuid4().hex,
                    reason=normalized_reason,
                    message_id=transaction.owner_message_id(),
                    created_at=datetime.now(UTC),
                )
                self.snapshots.insert(transaction.connection, successor)
                completed = successor.first_pending() is None
                updated = transaction.transition(
                    reason=(
                        RuntimeContextChangeReason.TRIAGE_DEVELOPMENT_COMPLETED
                        if completed
                        else RuntimeContextChangeReason.CANDIDATE_ACCEPTED
                    ),
                    mutate=lambda saved: self._accept_candidate(
                        saved,
                        candidate_commit_sha,
                        successor,
                        completed,
                        integrated_commit_sha,
                    ),
                )
            else:
                updated = transaction.transition(
                    reason=RuntimeContextChangeReason.CANDIDATE_REJECTED,
                    mutate=lambda saved: self._reject_candidate(
                        saved,
                        candidate_commit_sha,
                    ),
                )
        self.event_bus.publish(
            ExecutionEvent(
                triage_id=updated.triage_id,
                event_type=(
                    ExecutionEventType.CANDIDATE_ACCEPTED
                    if decision == "accept"
                    else ExecutionEventType.CANDIDATE_REJECTED
                ),
                payload={
                    "run_id": current.current_run_id,
                    "milestone_key": milestone.key,
                    "candidate_commit_sha": candidate_commit_sha,
                    "successor_snapshot_id": (
                        successor.snapshot_id if successor is not None else None
                    ),
                },
            )
        )
        if completed:
            self.event_bus.publish(
                ExecutionEvent(
                    triage_id=updated.triage_id,
                    event_type=ExecutionEventType.TRIAGE_DEVELOPMENT_COMPLETED,
                    payload={
                        "snapshot_id": updated.current_snapshot_id,
                        "candidate_commit_sha": candidate_commit_sha,
                    },
                )
            )
        next_milestone = successor.first_pending() if successor is not None else None
        return CandidateDecision(
            state=updated,
            decision=decision,
            milestone_key=milestone.key,
            candidate_commit_sha=candidate_commit_sha,
            snapshot=successor,
            next_milestone_key=(
                next_milestone.key
                if next_milestone is not None
                else (milestone.key if decision == "reject" else None)
            ),
            completed=completed,
        )

    def _queue_next_run(
        self,
        current: ProjectRuntimeState,
        snapshot: MilestoneSnapshot,
        milestone: Milestone,
        *,
        first_run: bool,
    ) -> MilestoneRunQueued:
        git = self.git
        try:
            git.assert_clean()
            branch = git.current_branch()
            input_commit_sha = git.head_sha()
        except GitRepositoryError as error:
            raise DeliveryError(str(error)) from error
        retry_from_blocked = not first_run and current.status == "BLOCKED"
        if retry_from_blocked:
            self._assert_retryable_blocked(current)
        if not first_run:
            if (
                current.status not in {"IN_PROGRESS", "BLOCKED"}
                or current.pending_action is not None
            ):
                raise DeliveryError(
                    "A later Milestone Run requires IN_PROGRESS or a retryable BLOCKED "
                    "project with no pending action"
                )
            if current.git_branch != branch or current.git_main_version != input_commit_sha:
                raise DeliveryError("Project target Git state changed outside Delivery")
        if current.current_run_id is not None and not retry_from_blocked:
            raise DeliveryError("Project already has an active Milestone Run")
        if current.current_candidate_commit_sha is not None:
            raise DeliveryError("Current Candidate must be decided before another Run")

        now = datetime.now(UTC)
        run_id = uuid4().hex
        stage = milestone.stages[0]
        stage_run = StageRun(
            stage_run_id=uuid4().hex,
            triage_id=current.triage_id,
            run_id=run_id,
            snapshot_id=snapshot.snapshot_id,
            milestone_key=milestone.key,
            stage_key=stage.key,
            status=StageRunStatus.QUEUED,
            input_commit_sha=input_commit_sha,
            output_commit_sha=None,
            failure=None,
            created_at=now,
        )
        with self.context.transaction() as transaction:
            latest = transaction.state()
            latest_snapshot = self._snapshot_for_context(
                latest,
                connection=transaction.connection,
            )
            latest_milestone = self._first_pending(latest_snapshot)
            if (
                latest_snapshot.snapshot_id != snapshot.snapshot_id
                or latest_milestone.key != milestone.key
            ):
                raise DeliveryError("Milestone selection changed while queueing its Run")
            if first_run:
                if (
                    latest.status != "READY"
                    or latest.pending_action != "FIRST_RUN_APPROVAL"
                    or latest.rolling_started_at is not None
                ):
                    raise DeliveryError("Project is no longer waiting for first Run approval")
            else:
                if retry_from_blocked:
                    self._assert_retryable_blocked(
                        latest,
                        connection=transaction.connection,
                    )
                elif (
                    latest.status != "IN_PROGRESS"
                    or latest.pending_action is not None
                    or latest.rolling_started_at is None
                ):
                    raise DeliveryError(
                        "Project is no longer ready for another Milestone Run"
                    )
            if latest.current_run_id is not None and not retry_from_blocked:
                raise DeliveryError("Project gained an active Run or Candidate")
            if latest.current_candidate_commit_sha is not None:
                raise DeliveryError("Project gained an active Run or Candidate")
            self.stage_runs.insert(transaction.connection, stage_run)
            updated = transaction.transition(
                reason=(
                    RuntimeContextChangeReason.FIRST_RUN_STARTED
                    if first_run
                    else RuntimeContextChangeReason.MILESTONE_RUN_QUEUED
                ),
                mutate=lambda saved: replace(
                    saved,
                    status="IN_PROGRESS",
                    pending_action=None,
                    git_branch=(branch if first_run else saved.git_branch),
                    git_main_version=(
                        input_commit_sha if first_run else saved.git_main_version
                    ),
                    rolling_started_at=(now if first_run else saved.rolling_started_at),
                    current_run_id=run_id,
                    current_milestone_key=milestone.key,
                    current_stage_key=stage.key,
                    current_candidate_commit_sha=None,
                ),
            )
        self.event_bus.publish(
            ExecutionEvent(
                triage_id=updated.triage_id,
                event_type=ExecutionEventType.MILESTONE_RUN_QUEUED,
                payload={
                    "run_id": run_id,
                    "stage_run_id": stage_run.stage_run_id,
                    "snapshot_id": snapshot.snapshot_id,
                    "milestone_key": milestone.key,
                    "stage_key": stage.key,
                    "input_commit_sha": input_commit_sha,
                    "first_run": first_run,
                },
            )
        )
        return MilestoneRunQueued(
            state=updated,
            snapshot=snapshot,
            milestone=milestone,
            stage=stage,
            stage_run=stage_run,
            first_run=first_run,
        )

    @staticmethod
    def _request_first_run(
        context: ProjectRuntimeState,
        snapshot: MilestoneSnapshot,
    ) -> ProjectRuntimeState:
        if (
            context.status != "TODO"
            or context.pending_action is not None
            or context.rolling_started_at is not None
            or context.current_run_id is not None
            or context.current_candidate_commit_sha is not None
        ):
            raise DeliveryError("Project changed while requesting its first Run")
        if context.current_snapshot_id != snapshot.snapshot_id:
            raise DeliveryError("Milestone Snapshot changed while requesting first Run")
        return replace(
            context,
            status="READY",
            pending_action="FIRST_RUN_APPROVAL",
        )

    def _snapshot_for_context(
        self,
        context: ProjectRuntimeState,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> MilestoneSnapshot:
        if context.current_plan_commit_sha is None:
            raise DeliveryError("Milestone delivery requires an approved Plan commit")
        if context.current_snapshot_id is None:
            raise DeliveryError("Milestone delivery requires a published Snapshot")
        if connection is None:
            with self.context.transaction() as transaction:
                snapshot = self.snapshots.get(
                    transaction.connection,
                    context.current_snapshot_id,
                )
        else:
            snapshot = self.snapshots.get(connection, context.current_snapshot_id)
        if snapshot is None:
            raise LookupError(
                f"Current Milestone Snapshot not found: {context.current_snapshot_id}"
            )
        if snapshot.triage_id != context.triage_id:
            raise DeliveryError("Current Milestone Snapshot belongs to another Triage")
        if snapshot.plan_commit_sha != context.current_plan_commit_sha:
            raise DeliveryError("Current Milestone Snapshot is bound to an outdated Plan")
        return snapshot

    @staticmethod
    def _first_pending(snapshot: MilestoneSnapshot) -> Milestone:
        milestone = snapshot.first_pending()
        if milestone is None:
            raise DeliveryError("Milestone Snapshot has no pending Milestone")
        return milestone

    def _stage_contract(
        self,
        connection: sqlite3.Connection,
        context: ProjectRuntimeState,
        stage_run: StageRun,
    ) -> tuple[MilestoneSnapshot, Milestone, Stage]:
        if context.status != "IN_PROGRESS" or context.pending_action is not None:
            raise DeliveryError("Stage execution requires an active IN_PROGRESS project")
        if context.current_candidate_commit_sha is not None:
            raise DeliveryError("Stage execution cannot continue with a pending Candidate")
        if (
            context.current_run_id != stage_run.run_id
            or context.current_snapshot_id != stage_run.snapshot_id
            or context.current_milestone_key != stage_run.milestone_key
            or context.current_stage_key != stage_run.stage_key
        ):
            raise DeliveryError("StageRun does not match the current delivery cursor")
        snapshot = self._snapshot_for_context(context, connection=connection)
        milestone = self._first_pending(snapshot)
        if milestone.key != stage_run.milestone_key:
            raise DeliveryError("StageRun is not for the first pending Milestone")
        stage = next(
            (item for item in milestone.stages if item.key == stage_run.stage_key),
            None,
        )
        if stage is None:
            raise DeliveryError("StageRun Stage is absent from its fixed Snapshot")
        return snapshot, milestone, stage

    @staticmethod
    def _advance_stage(
        context: ProjectRuntimeState,
        completed: StageRun,
        next_stage: Stage,
    ) -> ProjectRuntimeState:
        DeliveryService._assert_current_stage(context, completed)
        if context.current_candidate_commit_sha is not None:
            raise DeliveryError("Candidate appeared while advancing Stage execution")
        return replace(context, current_stage_key=next_stage.key)

    @staticmethod
    def _candidate_ready(
        context: ProjectRuntimeState,
        completed: StageRun,
        candidate_commit_sha: str,
    ) -> ProjectRuntimeState:
        DeliveryService._assert_current_stage(context, completed)
        if context.current_candidate_commit_sha is not None:
            raise DeliveryError("Project already has a pending Candidate")
        return replace(
            context,
            current_candidate_commit_sha=candidate_commit_sha,
        )

    @staticmethod
    def _stage_failed(
        context: ProjectRuntimeState,
        failed: StageRun,
    ) -> ProjectRuntimeState:
        DeliveryService._assert_current_stage(context, failed)
        return replace(context, status="BLOCKED")

    @staticmethod
    def _block_after_milestone_hard_gate_rejection(
        context: ProjectRuntimeState,
    ) -> ProjectRuntimeState:
        if context.status != "IN_PROGRESS" or context.pending_action is not None:
            raise DeliveryError(
                "Milestone Hard Gate rejection requires an idle IN_PROGRESS project"
            )
        if context.current_run_id is not None:
            raise DeliveryError(
                "Milestone Hard Gate rejection cannot interrupt an active Run"
            )
        return replace(context, status="BLOCKED")

    @staticmethod
    def _assert_current_stage(
        context: ProjectRuntimeState,
        stage_run: StageRun,
    ) -> None:
        if (
            context.status != "IN_PROGRESS"
            or context.current_run_id != stage_run.run_id
            or context.current_snapshot_id != stage_run.snapshot_id
            or context.current_milestone_key != stage_run.milestone_key
            or context.current_stage_key != stage_run.stage_key
        ):
            raise DeliveryError("Project delivery cursor changed during Stage execution")

    def _candidate_contract(
        self,
        context: ProjectRuntimeState,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> tuple[MilestoneSnapshot, Milestone, str]:
        if context.status != "IN_PROGRESS" or context.pending_action is not None:
            raise DeliveryError("Candidate decision requires an IN_PROGRESS project")
        if (
            context.current_run_id is None
            or context.current_milestone_key is None
            or context.current_candidate_commit_sha is None
        ):
            raise DeliveryError("Project has no unresolved Milestone Candidate")

        def load(
            opened: sqlite3.Connection,
        ) -> tuple[MilestoneSnapshot, Milestone, str]:
            snapshot = self._snapshot_for_context(context, connection=opened)
            milestone = self._first_pending(snapshot)
            if milestone.key != context.current_milestone_key:
                raise DeliveryError("Candidate is not for the first pending Milestone")
            stage_runs = self.stage_runs.list_by_run_id(
                opened,
                context.current_run_id or "",
            )
            if tuple(stage_run.stage_key for stage_run in stage_runs) != tuple(
                stage.key for stage in milestone.stages
            ):
                raise DeliveryError("Candidate Run does not contain every ordered Stage")
            if any(
                stage_run.status is not StageRunStatus.SUCCEEDED
                for stage_run in stage_runs
            ):
                raise DeliveryError("Candidate Run contains a non-succeeded Stage")
            candidate = context.current_candidate_commit_sha or ""
            if not stage_runs or stage_runs[-1].output_commit_sha != candidate:
                raise DeliveryError("Candidate does not match the final Stage output")
            return snapshot, milestone, candidate

        if connection is not None:
            return load(connection)
        with self.context.transaction() as transaction:
            return load(transaction.connection)

    def _assert_candidate_ref(
        self,
        context: ProjectRuntimeState,
        candidate_commit_sha: str,
    ) -> None:
        if context.current_run_id is None:
            raise DeliveryError("Candidate has no Run identity")
        try:
            referenced = self.git.resolve_ref(
                delivery_candidate_ref(context.current_run_id)
            )
        except GitRepositoryError as error:
            raise DeliveryError(str(error)) from error
        if referenced != candidate_commit_sha:
            raise DeliveryError("Candidate Git ref does not match Runtime State")

    @staticmethod
    def _accept_candidate(
        context: ProjectRuntimeState,
        candidate_commit_sha: str,
        successor: MilestoneSnapshot,
        completed: bool,
        integrated_commit_sha: str,
    ) -> ProjectRuntimeState:
        if context.current_candidate_commit_sha != candidate_commit_sha:
            raise DeliveryError("Candidate changed while being accepted")
        return replace(
            context,
            status="DONE" if completed else "IN_PROGRESS",
            git_main_version=integrated_commit_sha,
            current_snapshot_id=successor.snapshot_id,
            current_run_id=None,
            current_milestone_key=None,
            current_stage_key=None,
            current_candidate_commit_sha=None,
        )

    @staticmethod
    def _reject_candidate(
        context: ProjectRuntimeState,
        candidate_commit_sha: str,
    ) -> ProjectRuntimeState:
        if context.current_candidate_commit_sha != candidate_commit_sha:
            raise DeliveryError("Candidate changed while being rejected")
        return replace(
            context,
            current_run_id=None,
            current_milestone_key=None,
            current_stage_key=None,
            current_candidate_commit_sha=None,
        )

    def _assert_publishable(
        self,
        context: ProjectRuntimeState,
        milestones: tuple[Milestone, ...],
    ) -> MilestoneSnapshot | None:
        if context.current_plan_commit_sha is None:
            raise DeliveryError("Milestones require an approved Plan commit")
        if context.pending_action is not None:
            raise DeliveryError(
                "Milestones cannot be updated while waiting for "
                f"{context.pending_action}"
            )
        if context.status not in {"TODO", "IN_PROGRESS", "BLOCKED"}:
            raise DeliveryError(
                "Milestones cannot be updated from status " f"{context.status}"
            )
        if context.current_run_id is not None:
            if context.status != "BLOCKED":
                raise DeliveryError("Milestones cannot be updated during an active Run")
            self._assert_retryable_blocked(context)
        if context.current_candidate_commit_sha is not None:
            raise DeliveryError("Milestones cannot be updated while a Candidate is pending")
        if not milestones:
            raise DeliveryError("Milestone View must not be empty")
        if not any(
            milestone.state is MilestoneState.PENDING for milestone in milestones
        ):
            raise DeliveryError("Milestone View must contain a pending Milestone")
        if context.current_snapshot_id is None:
            if any(
                milestone.state is MilestoneState.COMPLETED for milestone in milestones
            ):
                raise DeliveryError(
                    "Initial Milestone View cannot mark a Milestone completed"
                )
            return None
        with self.context.transaction() as transaction:
            previous = self.snapshots.get(
                transaction.connection,
                context.current_snapshot_id,
            )
        if previous is None:
            raise LookupError(
                "Current Milestone Snapshot not found: " f"{context.current_snapshot_id}"
            )
        old_completed = tuple(
            milestone
            for milestone in previous.milestones
            if milestone.state is MilestoneState.COMPLETED
        )
        new_completed = tuple(
            milestone
            for milestone in milestones
            if milestone.state is MilestoneState.COMPLETED
        )
        if new_completed != old_completed:
            raise DeliveryError(
                "Milestone completion is only allowed by accepting its Candidate"
            )
        return previous

    def _assert_approved_specs(self, context: ProjectRuntimeState) -> None:
        plan_commit_sha = context.current_plan_commit_sha
        if plan_commit_sha is None:
            raise DeliveryError("Delivery requires an approved Plan commit")
        try:
            changed = self.git.paths_changed_from_commit(
                plan_commit_sha,
                self._spec_documents(),
            )
        except GitRepositoryError as error:
            raise DeliveryError(str(error)) from error
        if changed:
            raise DeliveryError(
                "Canonical Plan Specs changed after user approval; update the Specs "
                "and request Plan approval before continuing delivery: "
                + ", ".join(changed)
            )

    def _assert_candidate_preserves_specs(
        self,
        context: ProjectRuntimeState,
        candidate_commit_sha: str,
    ) -> None:
        plan_commit_sha = context.current_plan_commit_sha
        if plan_commit_sha is None:
            raise DeliveryError("Candidate acceptance requires an approved Plan commit")
        try:
            changed = self.git.paths_changed_from_commit(
                plan_commit_sha,
                self._spec_documents(),
                target_commit_sha=candidate_commit_sha,
            )
        except GitRepositoryError as error:
            raise DeliveryError(str(error)) from error
        if changed:
            raise DeliveryError(
                "Candidate changes canonical Plan Specs and cannot be accepted; reject "
                "it, update the Specs, and request Plan approval before retrying: "
                + ", ".join(changed)
            )

    def _assert_retryable_blocked(
        self,
        context: ProjectRuntimeState,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        if (
            context.status != "BLOCKED"
            or context.pending_action is not None
            or context.rolling_started_at is None
            or context.current_candidate_commit_sha is not None
        ):
            raise DeliveryError("Project is not a retryable failed delivery")
        if context.current_run_id is None:
            return

        def validate(opened: sqlite3.Connection) -> None:
            runs = self.stage_runs.list_by_run_id(opened, context.current_run_id or "")
            failed = runs[-1] if runs else None
            if (
                failed is None
                or failed.status is not StageRunStatus.FAILED
                or failed.stage_key != context.current_stage_key
                or failed.milestone_key != context.current_milestone_key
                or failed.snapshot_id != context.current_snapshot_id
            ):
                raise DeliveryError(
                    "BLOCKED delivery does not point to a terminal failed Stage"
                )

        if connection is not None:
            validate(connection)
            return
        with self.context.transaction() as transaction:
            validate(transaction.connection)

    def _run_milestone_hard_gate(
        self,
        context: ProjectRuntimeState,
        plan_commit_sha: str,
        milestones: tuple[Milestone, ...],
        subject_digest: str,
    ) -> MilestoneReviewResult:
        invocation_id = uuid4().hex
        self.event_bus.publish(
            ExecutionEvent(
                triage_id=context.triage_id,
                event_type=ExecutionEventType.AGENT_INVOCATION_STARTED,
                payload={
                    "invocation_id": invocation_id,
                    "operation": "milestone_hard_gate",
                    "subject_digest": subject_digest,
                },
            )
        )
        try:
            review = self.review_milestones(
                MilestoneReviewRequest(
                    triage_id=context.triage_id,
                    plan_commit_sha=plan_commit_sha,
                    milestones=milestones,
                    subject_digest=subject_digest,
                )
            )
            self._validate_review(review, subject_digest)
        except Exception as error:
            self.event_bus.publish(
                ExecutionEvent(
                    triage_id=context.triage_id,
                    event_type=ExecutionEventType.AGENT_INVOCATION_FAILED,
                    payload={
                        "invocation_id": invocation_id,
                        "operation": "milestone_hard_gate",
                        "subject_digest": subject_digest,
                        "failure_type": type(error).__name__,
                    },
                )
            )
            raise
        self.event_bus.publish(
            ExecutionEvent(
                triage_id=context.triage_id,
                event_type=ExecutionEventType.AGENT_INVOCATION_COMPLETED,
                payload={
                    "invocation_id": invocation_id,
                    "operation": "milestone_hard_gate",
                    "subject_digest": review.subject_digest,
                    "decision": review.decision,
                    "required_change_count": len(review.required_changes),
                    "review_artifact": {
                        "uri": review.audit_artifact.uri,
                        "project_relative_path": (
                            review.audit_artifact.project_relative_path
                        ),
                        "media_type": review.audit_artifact.media_type,
                        "size": review.audit_artifact.size,
                        "sha256": review.audit_artifact.sha256,
                    },
                },
            )
        )
        return review

    def _spec_documents(self) -> tuple[Path, ...]:
        return tuple(self.project_path / name for name in SPEC_DOCUMENT_NAMES)

    @staticmethod
    def _validate_review(
        review: MilestoneReviewResult,
        subject_digest: str,
    ) -> None:
        if review.subject_digest != subject_digest:
            raise DeliveryError("Milestone Hard Gate reviewed a different subject")
        if review.decision not in {"pass", "revise"}:
            raise DeliveryError("Milestone Hard Gate returned an invalid decision")
        if not review.summary.strip():
            raise DeliveryError("Milestone Hard Gate returned an empty summary")
        if review.decision == "pass" and review.required_changes:
            raise DeliveryError(
                "Milestone Hard Gate pass must not contain required changes"
            )
        if review.decision == "revise" and not review.required_changes:
            raise DeliveryError(
                "Milestone Hard Gate revise must contain required changes"
            )

    def _publish_snapshot(
        self,
        connection: sqlite3.Connection,
        context: ProjectRuntimeState,
        snapshot: MilestoneSnapshot,
    ) -> ProjectRuntimeState:
        if context.current_plan_commit_sha != snapshot.plan_commit_sha:
            raise DeliveryError("Approved Plan changed while publishing Milestones")
        if context.current_candidate_commit_sha is not None:
            raise DeliveryError("Project began delivery while publishing Milestones")
        if context.current_run_id is not None:
            self._assert_retryable_blocked(context, connection=connection)
        return replace(
            context,
            current_snapshot_id=snapshot.snapshot_id,
            current_run_id=None,
            current_milestone_key=None,
            current_stage_key=None,
        )

def delivery_run_ref(run_id: str) -> str:
    """Return the durable ref for an in-progress Run's latest Stage output."""
    _validate_ref_identifier(run_id)
    return f"refs/agentplanex/runs/{run_id}"


def delivery_candidate_ref(run_id: str) -> str:
    """Return the durable ref that keeps a decided-or-undecided Candidate reachable."""
    _validate_ref_identifier(run_id)
    return f"refs/agentplanex/candidates/{run_id}"


def _validate_ref_identifier(value: str) -> None:
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
    if not value or any(character not in allowed for character in value):
        raise ValueError(f"Run identifier contains unsupported characters: {value!r}")


def _stage_index(milestone: Milestone, stage_key: str) -> int:
    for index, stage in enumerate(milestone.stages):
        if stage.key == stage_key:
            return index
    raise LookupError(f"Stage not found in Milestone: {stage_key}")


def _candidate_ready_message(
    context: ProjectRuntimeState,
    milestone: Milestone,
    stage_run: StageRun,
    candidate_commit_sha: str,
) -> str:
    run_id = stage_run.run_id
    return json.dumps(
        {
            "event": "MILESTONE_CANDIDATE_READY",
            "work_object": {
                "snapshot_id": stage_run.snapshot_id,
                "run_id": run_id,
                "milestone_key": stage_run.milestone_key,
                "base_commit_sha": context.git_main_version,
                "candidate_commit_sha": candidate_commit_sha,
                "candidate_ref": delivery_candidate_ref(run_id),
            },
            "evidence": {
                "delivery_documents": [
                    f"docs/agentplanex/deliveries/{run_id}/{stage.key}.md"
                    for stage in milestone.stages
                ],
                "review_status": "NOT_REQUESTED",
            },
            "required_decision": (
                "Inspect the fixed Candidate, delegate a Reviewer when useful, then "
                "accept or reject it with decide_milestone_candidate. Afterwards "
                "reassess whether to run next, update Milestones, revise Specs, or "
                "return control to the user."
            ),
        },
        ensure_ascii=False,
        indent=2,
    )
