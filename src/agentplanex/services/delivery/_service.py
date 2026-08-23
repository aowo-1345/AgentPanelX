"""Milestone publication and rolling-delivery state transitions."""

import sqlite3
from dataclasses import InitVar, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
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
)
from agentplanex.services.delivery._driver import _StageDriver
from agentplanex.services.delivery._stage_executor import StageExecutor
from agentplanex.services.delivery.contracts import (
    BlockedRunApprovalRequested,
    CandidateDecision,
    DeliveryDriveOutcome,
    DeliveryError,
    DeliveryWorkState,
    FirstRunApprovalRequested,
    MilestoneHardGate,
    MilestoneReviewRequest,
    MilestoneReviewResult,
    MilestoneRunQueued,
    MilestonesUpdated,
    missing_milestone_hard_gate,
)
from agentplanex.services.delivery.models import (
    CandidateIdentity,
    Milestone,
    MilestoneSnapshot,
    MilestoneState,
    delivery_candidate_ref,
    milestone_view_digest,
)
from agentplanex.services.event_bus import EventBus
from agentplanex.services.planning.models import PLAN_DOCUMENT_NAMES
from agentplanex.services.project_runtime_context.context import ProjectRuntimeContext


@dataclass(slots=True)
class DeliveryService:
    """Own Snapshot publication now and the delivery state machine incrementally."""

    project_path: Path
    context: ProjectRuntimeContext
    git: GitRepository
    stage_executor: InitVar[StageExecutor]
    event_bus: EventBus = field(default_factory=EventBus)
    snapshots: SQLiteMilestoneSnapshotRepository = field(
        default_factory=SQLiteMilestoneSnapshotRepository
    )
    review_milestones: MilestoneHardGate = missing_milestone_hard_gate
    _driver: _StageDriver = field(init=False, repr=False)

    def __post_init__(self, stage_executor: StageExecutor) -> None:
        self._driver = _StageDriver(
            project_path=self.project_path,
            context=self.context,
            git=self.git,
            event_bus=self.event_bus,
            snapshots=self.snapshots,
            executor=stage_executor,
        )

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
                previous_snapshot_id=(previous.snapshot_id if previous is not None else None),
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

    def request_next_milestone(
        self,
    ) -> FirstRunApprovalRequested | BlockedRunApprovalRequested | MilestoneRunQueued:
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
            self._driver.assert_retryable_blocked(current)
            updated = self.context.transition(
                reason=RuntimeContextChangeReason.BLOCKED_RUN_APPROVAL_REQUESTED,
                mutate=lambda latest: replace(
                    latest,
                    pending_action="BLOCKED_RUN_APPROVAL",
                ),
            )
            self.event_bus.publish(
                ExecutionEvent(
                    triage_id=updated.triage_id,
                    event_type=ExecutionEventType.BLOCKED_RUN_APPROVAL_REQUESTED,
                    payload={
                        "snapshot_id": snapshot.snapshot_id,
                        "milestone_key": milestone.key,
                        "failed_run_id": current.current_run_id,
                        "failed_stage_key": current.current_stage_key,
                    },
                )
            )
            return BlockedRunApprovalRequested(updated, snapshot, milestone)
        return self._driver.queue_run(current, snapshot, milestone, first_run=False)

    def approve_blocked_run(self) -> MilestoneRunQueued:
        """Approve the Owner-selected retry through the canonical queue path."""
        current = self.context.state()
        snapshot = self._snapshot_for_context(current)
        milestone = self._first_pending(snapshot)
        self._assert_approved_specs(current)
        if current.pending_action != "BLOCKED_RUN_APPROVAL":
            raise DeliveryError("Project is not waiting for blocked Run approval")
        queued = self._driver.queue_run(
            current,
            snapshot,
            milestone,
            first_run=False,
            blocked_approval=True,
        )
        self.event_bus.publish(
            ExecutionEvent(
                triage_id=queued.state.triage_id,
                event_type=ExecutionEventType.BLOCKED_RUN_APPROVED,
                payload={
                    "run_id": queued.run_id,
                    "stage_run_id": queued.stage_run_id,
                    "snapshot_id": queued.snapshot_id,
                    "milestone_key": queued.milestone_key,
                    "stage_key": queued.stage_key,
                },
            )
        )
        return queued

    def reject_blocked_run(self, feedback: str) -> ProjectRuntimeState:
        """Reject the selected retry while preserving the failed cursor."""
        normalized = " ".join(feedback.split())
        if not normalized:
            raise DeliveryError("Blocked Run rejection feedback must not be empty")
        current = self.context.state()
        if current.status != "BLOCKED" or current.pending_action != "BLOCKED_RUN_APPROVAL":
            raise DeliveryError("Project is not waiting for blocked Run approval")
        updated = self.context.transition(
            reason=RuntimeContextChangeReason.BLOCKED_RUN_REJECTED,
            mutate=lambda latest: replace(latest, pending_action=None),
        )
        self.event_bus.publish(
            ExecutionEvent(
                triage_id=updated.triage_id,
                event_type=ExecutionEventType.BLOCKED_RUN_REJECTED,
                payload={"feedback": normalized},
            )
        )
        return updated

    def start_first_run(self) -> MilestoneRunQueued:
        """Apply the user's one-time explicit Start and queue the first Stage."""
        current = self.context.state()
        snapshot = self._snapshot_for_context(current)
        milestone = self._first_pending(snapshot)
        self._assert_approved_specs(current)
        if (
            current.status != "READY"
            or current.pending_action != "FIRST_RUN_APPROVAL"
            or current.rolling_started_at is not None
        ):
            raise DeliveryError("Project is not waiting for its first Run approval")
        return self._driver.queue_run(current, snapshot, milestone, first_run=True)

    def active_work(self) -> DeliveryWorkState:
        """Return only whether Delivery is idle, runnable, or currently running."""
        return self._driver.active_work()

    def drive_next(self) -> DeliveryDriveOutcome:
        """Drive at most one Stage through the private three-phase driver."""
        return self._driver.drive_next()

    def reconcile_interrupted_work(
        self,
        *,
        finished_at: datetime,
        failure: str,
    ) -> bool:
        """Terminalize interrupted Stage work through Delivery's own transaction."""
        return self._driver.reconcile_interrupted_work(
            finished_at=finished_at,
            failure=failure,
        )

    def decide_milestone_candidate(
        self,
        *,
        expected: CandidateIdentity,
        decision: Literal["accept", "reject"],
        reason: str,
    ) -> CandidateDecision:
        """Accept or reject the fixed Candidate without letting the Owner mutate Git."""
        normalized_reason = " ".join(reason.split())
        if not normalized_reason:
            raise DeliveryError("Candidate decision reason must not be empty")
        if decision not in {"accept", "reject"}:
            raise DeliveryError("Candidate decision must be accept or reject")
        current = self.context.state()
        snapshot, milestone, candidate_commit_sha = self._candidate_contract(
            current,
            expected,
        )
        self._assert_candidate_ref(expected)
        completed = False
        if decision == "accept":
            self._assert_candidate_preserves_specs(current, candidate_commit_sha)
            integrated_commit_sha = self._integrate_candidate(current, expected)
        else:
            self._assert_candidate_target(current, expected, accepted=False)

        with self.context.transaction() as transaction:
            latest = transaction.state()
            latest_snapshot, latest_milestone, latest_candidate = self._candidate_contract(
                latest,
                expected,
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
                successor = MilestoneSnapshot(
                    snapshot_id=uuid4().hex,
                    triage_id=latest_snapshot.triage_id,
                    previous_snapshot_id=latest_snapshot.snapshot_id,
                    plan_commit_sha=latest_snapshot.plan_commit_sha,
                    milestones=latest_snapshot.milestones,
                    reason=normalized_reason,
                    message_id=transaction.owner_message_id(),
                    created_at=datetime.now(UTC),
                )
                self.snapshots.insert(transaction.connection, successor)
                updated = transaction.transition(
                    reason=RuntimeContextChangeReason.CANDIDATE_REJECTED,
                    mutate=lambda saved: self._reject_candidate(
                        saved,
                        candidate_commit_sha,
                        successor,
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
                    "successor_snapshot_id": successor.snapshot_id,
                    "reason": normalized_reason,
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
        next_milestone = successor.first_pending()
        return CandidateDecision(
            state=updated,
            identity=expected,
            decision=decision,
            result_snapshot_id=successor.snapshot_id,
            next_milestone_key=(
                next_milestone.key
                if next_milestone is not None
                else (milestone.key if decision == "reject" else None)
            ),
            completed=completed,
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

    @staticmethod
    def _block_after_milestone_hard_gate_rejection(
        context: ProjectRuntimeState,
    ) -> ProjectRuntimeState:
        if context.status != "IN_PROGRESS" or context.pending_action is not None:
            raise DeliveryError(
                "Milestone Hard Gate rejection requires an idle IN_PROGRESS project"
            )
        if context.current_run_id is not None:
            raise DeliveryError("Milestone Hard Gate rejection cannot interrupt an active Run")
        return replace(context, status="BLOCKED")

    def _candidate_contract(
        self,
        context: ProjectRuntimeState,
        expected: CandidateIdentity,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> tuple[MilestoneSnapshot, Milestone, str]:
        return self._driver.candidate_contract(
            context,
            expected,
            connection=connection,
        )

    def _assert_candidate_ref(
        self,
        expected: CandidateIdentity,
    ) -> None:
        try:
            referenced = self.git.resolve_ref(delivery_candidate_ref(expected.run_id))
        except GitRepositoryError as error:
            raise DeliveryError(str(error)) from error
        if referenced != expected.candidate_commit_sha:
            raise DeliveryError("Candidate Git ref does not match Runtime State")

    def _integrate_candidate(
        self,
        context: ProjectRuntimeState,
        expected: CandidateIdentity,
    ) -> str:
        head = self._assert_candidate_target(context, expected, accepted=None)
        if head == context.git_main_version:
            try:
                self.git.integrate_fast_forward(
                    expected.candidate_commit_sha,
                    expected_branch=context.git_branch or "",
                    expected_head=context.git_main_version,
                )
            except GitRepositoryError as error:
                try:
                    recovered = self._assert_candidate_target(
                        context,
                        expected,
                        accepted=True,
                    )
                except DeliveryError:
                    raise DeliveryError(str(error)) from error
                if recovered != expected.candidate_commit_sha:
                    raise DeliveryError(str(error)) from error
        self._assert_candidate_ref(expected)
        return self._assert_candidate_target(context, expected, accepted=True)

    def _assert_candidate_target(
        self,
        context: ProjectRuntimeState,
        expected: CandidateIdentity,
        *,
        accepted: bool | None,
    ) -> str:
        if context.git_branch is None or context.git_main_version is None:
            raise DeliveryError("Candidate has no fixed target branch and commit")
        try:
            self.git.assert_clean()
            if self.git.current_branch() != context.git_branch:
                raise DeliveryError("Project target branch changed during delivery")
            head = self.git.head_sha()
        except GitRepositoryError as error:
            raise DeliveryError(str(error)) from error
        allowed = (
            {context.git_main_version, expected.candidate_commit_sha}
            if accepted is None
            else ({expected.candidate_commit_sha} if accepted else {context.git_main_version})
        )
        if head not in allowed:
            raise DeliveryError("Project target HEAD changed during Candidate decision")
        return head

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
        successor: MilestoneSnapshot,
    ) -> ProjectRuntimeState:
        if context.current_candidate_commit_sha != candidate_commit_sha:
            raise DeliveryError("Candidate changed while being rejected")
        return replace(
            context,
            status="IN_PROGRESS",
            current_snapshot_id=successor.snapshot_id,
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
                f"Milestones cannot be updated while waiting for {context.pending_action}"
            )
        if context.status not in {"TODO", "IN_PROGRESS", "BLOCKED"}:
            raise DeliveryError(f"Milestones cannot be updated from status {context.status}")
        if context.current_run_id is not None:
            if context.status != "BLOCKED":
                raise DeliveryError("Milestones cannot be updated during an active Run")
            self._driver.assert_retryable_blocked(context)
        if context.current_candidate_commit_sha is not None:
            raise DeliveryError("Milestones cannot be updated while a Candidate is pending")
        if not milestones:
            raise DeliveryError("Milestone View must not be empty")
        if not any(milestone.state is MilestoneState.PENDING for milestone in milestones):
            raise DeliveryError("Milestone View must contain a pending Milestone")
        if context.current_snapshot_id is None:
            if any(milestone.state is MilestoneState.COMPLETED for milestone in milestones):
                raise DeliveryError("Initial Milestone View cannot mark a Milestone completed")
            return None
        with self.context.transaction() as transaction:
            previous = self.snapshots.get(
                transaction.connection,
                context.current_snapshot_id,
            )
        if previous is None:
            raise LookupError(
                f"Current Milestone Snapshot not found: {context.current_snapshot_id}"
            )
        old_completed = tuple(
            milestone
            for milestone in previous.milestones
            if milestone.state is MilestoneState.COMPLETED
        )
        new_completed = tuple(
            milestone for milestone in milestones if milestone.state is MilestoneState.COMPLETED
        )
        if new_completed != old_completed:
            raise DeliveryError("Milestone completion is only allowed by accepting its Candidate")
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
                "and request Plan approval before continuing delivery: " + ", ".join(changed)
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
                        "project_relative_path": (review.audit_artifact.project_relative_path),
                        "media_type": review.audit_artifact.media_type,
                        "size": review.audit_artifact.size,
                        "sha256": review.audit_artifact.sha256,
                    },
                },
            )
        )
        return review

    def _spec_documents(self) -> tuple[Path, ...]:
        return tuple(self.project_path / name for name in PLAN_DOCUMENT_NAMES)

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
            raise DeliveryError("Milestone Hard Gate pass must not contain required changes")
        if review.decision == "revise" and not review.required_changes:
            raise DeliveryError("Milestone Hard Gate revise must contain required changes")

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
            self._driver.assert_retryable_blocked(
                context,
                connection=connection,
            )
        return replace(
            context,
            current_snapshot_id=snapshot.snapshot_id,
            current_run_id=None,
            current_milestone_key=None,
            current_stage_key=None,
        )
