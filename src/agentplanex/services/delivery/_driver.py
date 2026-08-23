"""Private three-phase driver for durable Delivery StageRuns."""

import json
import sqlite3
from contextlib import suppress
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from uuid import uuid4

from agentplanex.domains.execution_event import (
    ExecutionEvent,
    ExecutionEventType,
    RuntimeContextChangeReason,
)
from agentplanex.domains.project_runtime_state import ProjectRuntimeState
from agentplanex.infrastructure.git_repository import GitRepository, GitRepositoryError
from agentplanex.infrastructure.sqlite.repositories import (
    SQLiteMilestoneSnapshotRepository,
    SQLiteStageRunRepository,
)
from agentplanex.services.delivery._stage_executor import (
    StageExecutionRequest,
    StageExecutor,
)
from agentplanex.services.delivery.contracts import (
    DeliveryDriveOutcome,
    DeliveryError,
    DeliveryWorkState,
    MilestoneRunQueued,
)
from agentplanex.services.delivery.models import (
    CandidateIdentity,
    Milestone,
    MilestoneSnapshot,
    Stage,
    StageRun,
    StageRunStatus,
    delivery_candidate_ref,
    delivery_run_ref,
)
from agentplanex.services.event_bus import EventBus
from agentplanex.services.project_runtime_context.context import ProjectRuntimeContext
from agentplanex.services.project_runtime_context.models import (
    ProjectOwnerTask,
    ProjectOwnerTaskType,
)


@dataclass(frozen=True, slots=True)
class _StageClaim:
    state: ProjectRuntimeState
    snapshot: MilestoneSnapshot
    milestone: Milestone
    stage: Stage
    stage_run: StageRun


@dataclass(frozen=True, slots=True)
class _StageCompletion:
    state: ProjectRuntimeState
    stage_run: StageRun
    next_stage_run: StageRun | None
    candidate_commit_sha: str | None


@dataclass(slots=True)
class _StageDriver:
    """Own Stage persistence, long execution, worktrees, and delivery refs."""

    project_path: Path
    context: ProjectRuntimeContext
    git: GitRepository
    event_bus: EventBus
    snapshots: SQLiteMilestoneSnapshotRepository
    executor: StageExecutor
    lease_duration: timedelta = timedelta(minutes=30)
    stage_runs: SQLiteStageRunRepository = field(default_factory=SQLiteStageRunRepository)

    def __post_init__(self) -> None:
        if self.lease_duration <= timedelta(0):
            raise ValueError("StageRun lease duration must be positive")

    def active_work(self) -> DeliveryWorkState:
        """Return only the scheduling fact Runtime needs."""
        active = self._active_stage_run()
        if active is None:
            return DeliveryWorkState.IDLE
        if active.status is StageRunStatus.QUEUED:
            return DeliveryWorkState.RUNNABLE
        if active.lease_expires_at is not None and active.lease_expires_at <= datetime.now(UTC):
            return DeliveryWorkState.RUNNABLE
        return DeliveryWorkState.RUNNING

    def queue_run(
        self,
        current: ProjectRuntimeState,
        snapshot: MilestoneSnapshot,
        milestone: Milestone,
        *,
        first_run: bool,
        blocked_approval: bool = False,
    ) -> MilestoneRunQueued:
        """Fix one Run identity and atomically queue its first Stage."""
        try:
            self.git.assert_clean()
            branch = self.git.current_branch()
            input_commit_sha = self.git.head_sha()
        except GitRepositoryError as error:
            raise DeliveryError(str(error)) from error
        retry_from_blocked = not first_run and current.status == "BLOCKED"
        if first_run and current.current_plan_commit_sha != input_commit_sha:
            raise DeliveryError("Project target Git state changed after Plan approval")
        if retry_from_blocked:
            self.assert_retryable_blocked(
                current,
                allow_pending_approval=blocked_approval,
            )
        if not first_run:
            if current.status not in {"IN_PROGRESS", "BLOCKED"} or (
                current.pending_action is not None
                and not (blocked_approval and current.pending_action == "BLOCKED_RUN_APPROVAL")
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
            elif retry_from_blocked:
                self.assert_retryable_blocked(
                    latest,
                    connection=transaction.connection,
                    allow_pending_approval=blocked_approval,
                )
            elif (
                latest.status != "IN_PROGRESS"
                or latest.pending_action is not None
                or latest.rolling_started_at is None
            ):
                raise DeliveryError("Project is no longer ready for another Milestone Run")
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
                    git_main_version=(input_commit_sha if first_run else saved.git_main_version),
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
            run_id=run_id,
            stage_run_id=stage_run.stage_run_id,
            snapshot_id=snapshot.snapshot_id,
            milestone_key=milestone.key,
            stage_key=stage.key,
            input_commit_sha=input_commit_sha,
            first_run=first_run,
        )

    def drive_next(self) -> DeliveryDriveOutcome:
        """Claim, execute, and terminalize at most one Stage outside long DB writes."""
        active = self._active_stage_run()
        if active is None:
            return DeliveryDriveOutcome.IDLE
        now = datetime.now(UTC)
        if active.status is StageRunStatus.RUNNING:
            if active.lease_expires_at is None or active.lease_expires_at > now:
                raise DeliveryError(
                    "StageRun is already running; wait for its lease or terminal result"
                )
            completion = self._fail_stage(
                active.stage_run_id,
                failure="Stage execution lease expired before a terminal result",
                finished_at=now,
            )
            self._publish_stage_failed(completion.stage_run)
            self._remove_worktree(active.run_id)
            return DeliveryDriveOutcome.STAGE_FAILED

        claim = self._claim_next_stage(
            started_at=now,
            lease_expires_at=now + self.lease_duration,
        )
        invocation_id = uuid4().hex
        invocation_started = False
        candidate_ref_created = False
        output_commit_sha: str | None = None
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
            self.event_bus.publish(
                ExecutionEvent(
                    triage_id=claim.state.triage_id,
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
            _validate_stage_output(
                worktree_git,
                claim.stage_run,
                delivery_document,
            )
            output_commit_sha = worktree_git.commit_all(
                message=(
                    f"stage: {claim.milestone.key}/{claim.stage.key} ({claim.stage_run.run_id})"
                )
            )
            first_stage = claim.milestone.stages[0].key == claim.stage.key
            self.git.compare_and_swap_ref(
                delivery_run_ref(claim.stage_run.run_id),
                output_commit_sha,
                expected_sha=(None if first_stage else claim.stage_run.input_commit_sha),
            )
            if _is_final_stage(claim.milestone, claim.stage.key):
                self.git.compare_and_swap_ref(
                    delivery_candidate_ref(claim.stage_run.run_id),
                    output_commit_sha,
                    expected_sha=None,
                )
                candidate_ref_created = True
            completion = self._succeed_stage(
                claim.stage_run.stage_run_id,
                output_commit_sha=output_commit_sha,
                finished_at=datetime.now(UTC),
            )
        except Exception as error:
            return self._record_drive_failure(
                claim,
                error,
                invocation_id=invocation_id,
                invocation_started=invocation_started,
                candidate_ref_created=candidate_ref_created,
                candidate_commit_sha=output_commit_sha,
            )

        self._publish_invocation_completed(invocation_id, completion.stage_run)
        self._publish_stage_succeeded(completion)
        if completion.candidate_commit_sha is not None:
            self._remove_worktree(claim.stage_run.run_id)
            return DeliveryDriveOutcome.CANDIDATE_READY
        return DeliveryDriveOutcome.STAGE_SUCCEEDED

    def reconcile_interrupted_work(
        self,
        *,
        finished_at: datetime,
        failure: str,
    ) -> bool:
        """Atomically terminalize active Stage work and block this delivery."""
        with self.context.transaction() as transaction:
            state = transaction.state()
            failed = self.stage_runs.fail_active(
                transaction.connection,
                state.triage_id,
                finished_at=finished_at,
                failure=failure,
            )
            if not failed:
                return False
            transaction.transition(
                reason=RuntimeContextChangeReason.INTERRUPTED_WORK_FAILED,
                mutate=_block_runtime_execution,
            )
        for stage_run in failed:
            self.event_bus.publish(_interrupted_stage_event(stage_run))
        for run_id in {stage_run.run_id for stage_run in failed}:
            self._remove_worktree(run_id)
        return True

    def candidate_contract(
        self,
        context: ProjectRuntimeState,
        expected: CandidateIdentity,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> tuple[MilestoneSnapshot, Milestone, str]:
        """Validate the complete successful Stage chain for one Candidate."""
        if context.status not in {"IN_PROGRESS", "BLOCKED"} or context.pending_action is not None:
            raise DeliveryError("Candidate decision requires an active delivery project")
        if (
            context.current_snapshot_id != expected.snapshot_id
            or context.current_run_id != expected.run_id
            or context.current_milestone_key != expected.milestone_key
            or context.current_candidate_commit_sha != expected.candidate_commit_sha
        ):
            raise DeliveryError("Candidate decision identity is stale")
        if context.git_main_version is None:
            raise DeliveryError("Candidate has no fixed Git baseline")

        def load(opened: sqlite3.Connection) -> tuple[MilestoneSnapshot, Milestone, str]:
            snapshot = self._snapshot_for_context(context, connection=opened)
            milestone = self._first_pending(snapshot)
            if milestone.key != context.current_milestone_key:
                raise DeliveryError("Candidate is not for the first pending Milestone")
            if context.current_stage_key != milestone.stages[-1].key:
                raise DeliveryError("Candidate cursor is not at the final Stage")
            stage_runs = self.stage_runs.list_by_run_id(opened, expected.run_id)
            if any(
                stage_run.triage_id != context.triage_id
                or stage_run.snapshot_id != expected.snapshot_id
                or stage_run.run_id != expected.run_id
                or stage_run.milestone_key != expected.milestone_key
                for stage_run in stage_runs
            ):
                raise DeliveryError("Candidate Run provenance is inconsistent")
            if tuple(stage_run.stage_key for stage_run in stage_runs) != tuple(
                stage.key for stage in milestone.stages
            ):
                raise DeliveryError("Candidate Run does not contain every ordered Stage")
            if any(stage_run.status is not StageRunStatus.SUCCEEDED for stage_run in stage_runs):
                raise DeliveryError("Candidate Run contains a non-succeeded Stage")
            if stage_runs and stage_runs[0].input_commit_sha != context.git_main_version:
                raise DeliveryError("Candidate Run does not start at the Git baseline")
            if any(
                current.input_commit_sha != previous.output_commit_sha
                for previous, current in pairwise(stage_runs)
            ):
                raise DeliveryError("Candidate Run commit chain is discontinuous")
            candidate = expected.candidate_commit_sha
            if not stage_runs or stage_runs[-1].output_commit_sha != candidate:
                raise DeliveryError("Candidate does not match the final Stage output")
            return snapshot, milestone, candidate

        if connection is not None:
            return load(connection)
        with self.context.transaction() as transaction:
            return load(transaction.connection)

    def assert_retryable_blocked(
        self,
        context: ProjectRuntimeState,
        *,
        connection: sqlite3.Connection | None = None,
        allow_pending_approval: bool = False,
    ) -> None:
        """Validate that BLOCKED identifies one terminal failed delivery cursor."""
        if (
            context.status != "BLOCKED"
            or context.pending_action
            not in ({None, "BLOCKED_RUN_APPROVAL"} if allow_pending_approval else {None})
            or context.rolling_started_at is None
            or context.current_candidate_commit_sha is not None
        ):
            raise DeliveryError("Project is not a retryable failed delivery")
        if context.current_run_id is None:
            raise DeliveryError("BLOCKED delivery has no failed Run cursor")

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
                raise DeliveryError("BLOCKED delivery does not point to a terminal failed Stage")

        if connection is not None:
            validate(connection)
            return
        with self.context.transaction() as transaction:
            validate(transaction.connection)

    def _active_stage_run(self) -> StageRun | None:
        with self.context.transaction() as transaction:
            state = transaction.state()
            return self.stage_runs.get_active(transaction.connection, state.triage_id)

    def _claim_next_stage(
        self,
        *,
        started_at: datetime,
        lease_expires_at: datetime,
    ) -> _StageClaim:
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
        return _StageClaim(
            state=current,
            snapshot=snapshot,
            milestone=milestone,
            stage=stage,
            stage_run=claimed,
        )

    def _succeed_stage(
        self,
        stage_run_id: str,
        *,
        output_commit_sha: str,
        finished_at: datetime,
    ) -> _StageCompletion:
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
                    mutate=lambda latest: _advance_stage(latest, running, next_stage),
                )
            else:
                candidate_commit_sha = output_commit_sha
                updated = transaction.transition(
                    reason=RuntimeContextChangeReason.CANDIDATE_READY,
                    mutate=lambda latest: _candidate_ready(
                        latest,
                        running,
                        output_commit_sha,
                    ),
                )
                transaction.submit_owner_input(
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
        return _StageCompletion(
            state=updated,
            stage_run=succeeded,
            next_stage_run=next_stage_run,
            candidate_commit_sha=candidate_commit_sha,
        )

    def _fail_stage(
        self,
        stage_run_id: str,
        *,
        failure: str,
        finished_at: datetime,
    ) -> _StageCompletion:
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
                mutate=lambda latest: _stage_failed(latest, running),
            )
        return _StageCompletion(
            state=updated,
            stage_run=failed,
            next_stage_run=None,
            candidate_commit_sha=None,
        )

    def _record_drive_failure(
        self,
        claim: _StageClaim,
        error: Exception,
        *,
        invocation_id: str,
        invocation_started: bool,
        candidate_ref_created: bool,
        candidate_commit_sha: str | None,
    ) -> DeliveryDriveOutcome:
        completion = self._fail_stage(
            claim.stage_run.stage_run_id,
            failure=_failure_message(error),
            finished_at=datetime.now(UTC),
        )
        if candidate_ref_created:
            if candidate_commit_sha is None:
                raise RuntimeError("Created Candidate ref has no known commit")
            with suppress(GitRepositoryError):
                self.git.delete_ref(
                    delivery_candidate_ref(claim.stage_run.run_id),
                    expected_sha=candidate_commit_sha,
                )
        if invocation_started:
            self.event_bus.publish(
                ExecutionEvent(
                    triage_id=claim.state.triage_id,
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
        self._publish_stage_failed(completion.stage_run)
        self._remove_worktree(claim.stage_run.run_id)
        return DeliveryDriveOutcome.STAGE_FAILED

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

    def _snapshot_for_context(
        self,
        context: ProjectRuntimeState,
        *,
        connection: sqlite3.Connection,
    ) -> MilestoneSnapshot:
        if context.current_plan_commit_sha is None:
            raise DeliveryError("Milestone delivery requires an approved Plan commit")
        if context.current_snapshot_id is None:
            raise DeliveryError("Milestone delivery requires a published Snapshot")
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

    def _publish_invocation_completed(
        self,
        invocation_id: str,
        stage_run: StageRun,
    ) -> None:
        self.event_bus.publish(
            ExecutionEvent(
                triage_id=stage_run.triage_id,
                event_type=ExecutionEventType.AGENT_INVOCATION_COMPLETED,
                payload={
                    "invocation_id": invocation_id,
                    "operation": "stage_executor",
                    "stage_run_id": stage_run.stage_run_id,
                    "run_id": stage_run.run_id,
                    "output_commit_sha": stage_run.output_commit_sha,
                },
            )
        )

    def _publish_stage_succeeded(self, completion: _StageCompletion) -> None:
        succeeded = completion.stage_run
        self.event_bus.publish(
            ExecutionEvent(
                triage_id=completion.state.triage_id,
                event_type=ExecutionEventType.STAGE_RUN_SUCCEEDED,
                payload={
                    "stage_run_id": succeeded.stage_run_id,
                    "run_id": succeeded.run_id,
                    "milestone_key": succeeded.milestone_key,
                    "stage_key": succeeded.stage_key,
                    "input_commit_sha": succeeded.input_commit_sha,
                    "output_commit_sha": succeeded.output_commit_sha,
                    "next_stage_run_id": (
                        completion.next_stage_run.stage_run_id
                        if completion.next_stage_run is not None
                        else None
                    ),
                },
            )
        )
        if completion.candidate_commit_sha is not None:
            self.event_bus.publish(
                ExecutionEvent(
                    triage_id=completion.state.triage_id,
                    event_type=ExecutionEventType.CANDIDATE_READY,
                    payload={
                        "run_id": succeeded.run_id,
                        "milestone_key": succeeded.milestone_key,
                        "candidate_commit_sha": completion.candidate_commit_sha,
                    },
                )
            )

    def _publish_stage_failed(self, stage_run: StageRun) -> None:
        self.event_bus.publish(
            ExecutionEvent(
                triage_id=stage_run.triage_id,
                event_type=ExecutionEventType.STAGE_RUN_FAILED,
                payload={
                    "stage_run_id": stage_run.stage_run_id,
                    "run_id": stage_run.run_id,
                    "milestone_key": stage_run.milestone_key,
                    "stage_key": stage_run.stage_key,
                    "input_commit_sha": stage_run.input_commit_sha,
                },
            )
        )

    def _remove_worktree(self, run_id: str) -> None:
        try:
            self.git.remove_delivery_worktree(run_id)
        except GitRepositoryError:
            return


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


def _advance_stage(
    context: ProjectRuntimeState,
    completed: StageRun,
    next_stage: Stage,
) -> ProjectRuntimeState:
    _assert_current_stage(context, completed)
    if context.current_candidate_commit_sha is not None:
        raise DeliveryError("Candidate appeared while advancing Stage execution")
    return replace(context, current_stage_key=next_stage.key)


def _candidate_ready(
    context: ProjectRuntimeState,
    completed: StageRun,
    candidate_commit_sha: str,
) -> ProjectRuntimeState:
    _assert_current_stage(context, completed)
    if context.current_candidate_commit_sha is not None:
        raise DeliveryError("Project already has a pending Candidate")
    return replace(context, current_candidate_commit_sha=candidate_commit_sha)


def _stage_failed(
    context: ProjectRuntimeState,
    failed: StageRun,
) -> ProjectRuntimeState:
    _assert_current_stage(context, failed)
    return replace(context, status="BLOCKED")


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


def _block_runtime_execution(context: ProjectRuntimeState) -> ProjectRuntimeState:
    if context.status == "BLOCKED":
        return context
    if context.status == "DONE":
        raise ValueError("Completed Project Runtime cannot contain failed work")
    return replace(context, status="BLOCKED")


def _interrupted_stage_event(stage_run: StageRun) -> ExecutionEvent:
    if stage_run.status is not StageRunStatus.FAILED or stage_run.failure is None:
        raise ValueError("Interrupted Stage event requires a failed StageRun")
    return ExecutionEvent(
        triage_id=stage_run.triage_id,
        event_type=ExecutionEventType.STAGE_RUN_FAILED,
        payload={
            "stage_run_id": stage_run.stage_run_id,
            "run_id": stage_run.run_id,
            "snapshot_id": stage_run.snapshot_id,
            "milestone_key": stage_run.milestone_key,
            "stage_key": stage_run.stage_key,
            "input_commit_sha": stage_run.input_commit_sha,
            "failure": stage_run.failure,
            "interrupted": True,
            "started": stage_run.started_at is not None,
        },
    )


def _delivery_document_path(worktree: Path, run_id: str, stage_key: str) -> Path:
    return worktree / "docs" / "agentplanex" / "deliveries" / run_id / f"{stage_key}.md"


def _is_final_stage(milestone: Milestone, stage_key: str) -> bool:
    return milestone.stages[-1].key == stage_key


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


def _failure_message(error: Exception) -> str:
    detail = " ".join(str(error).split())
    return f"{type(error).__name__}: {detail}"[:2_000]
