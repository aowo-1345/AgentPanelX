"""Plan approval workflow over immutable subjects, Git, and Runtime Context."""

import json
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal
from uuid import uuid4

from agentplanex.domains.execution_event import (
    ExecutionEvent,
    ExecutionEventType,
    ProjectOwnerTask,
    ProjectOwnerTaskType,
    RuntimeContextChangeReason,
)
from agentplanex.domains.owner_activation import OwnerActivation
from agentplanex.domains.plan import PLAN_DOCUMENT_NAMES, PlanSubject
from agentplanex.domains.project_runtime_state import ProjectRuntimeState
from agentplanex.infrastructure.git_repository import GitRepository
from agentplanex.services.event_bus import EventBus
from agentplanex.services.planning._subject import (
    freeze_commit_subject,
    freeze_worktree_subject,
    plan_document_paths,
)
from agentplanex.services.planning.contracts import (
    PlanApprovalRequest,
    PlanDecision,
    PlanHardGate,
    PlanningError,
    PlanReviewRequest,
    PlanReviewResult,
    missing_plan_hard_gate,
)
from agentplanex.services.project_runtime_context import ProjectRuntimeContext

_PLAN_CHECKPOINT_MESSAGE = "plan: checkpoint specifications"


@dataclass(slots=True)
class PlanningService:
    """Own the complete decision loop for one exact Plan subject."""

    project_path: Path
    context: ProjectRuntimeContext
    git: GitRepository
    review_plan: PlanHardGate = missing_plan_hard_gate
    event_bus: EventBus = field(default_factory=EventBus)

    def request_plan_approval(self) -> PlanApprovalRequest:
        before = self.context.state()
        self._assert_requestable(before)
        subject = freeze_worktree_subject(self.project_path)
        review = self._run_hard_gate(before, subject) if before.status == "IN_PROGRESS" else None

        after = self.context.state()
        self._assert_requestable(after)
        if freeze_worktree_subject(self.project_path).digest != subject.digest:
            raise PlanningError("Plan specification documents changed while requesting approval")
        if review is not None and review.decision == "revise":
            return PlanApprovalRequest(
                state=after,
                accepted=False,
                subject_digest=subject.digest,
                review=review,
            )

        def request(current: ProjectRuntimeState) -> ProjectRuntimeState:
            self._assert_requestable(current)
            return replace(
                current,
                status=("TODO" if current.status == "TRIAGE" else current.status),
                pending_action="PLAN_APPROVAL",
                pending_plan_subject_digest=subject.digest,
            )

        updated = self.context.transition(
            reason=RuntimeContextChangeReason.PLAN_APPROVAL_REQUESTED,
            mutate=request,
        )
        self.event_bus.publish(
            ExecutionEvent(
                triage_id=updated.triage_id,
                event_type=ExecutionEventType.PLAN_APPROVAL_REQUESTED,
                payload={
                    "subject_digest": subject.digest,
                    "hard_gate_invoked": review is not None,
                },
            )
        )
        return PlanApprovalRequest(
            state=updated,
            accepted=True,
            subject_digest=subject.digest,
            review=review,
        )

    def approve_plan(self) -> PlanDecision:
        pending = self._assert_plan_pending()
        expected_digest = pending.pending_plan_subject_digest
        if expected_digest is None:
            raise PlanningError("Plan approval has no reviewed subject identity")
        if freeze_worktree_subject(self.project_path).digest != expected_digest:
            raise PlanningError("Plan specification documents changed after approval was requested")

        commit_sha = self._checkpoint_plan(pending, expected_digest)
        if freeze_commit_subject(self.project_path, self.git, commit_sha).digest != expected_digest:
            raise PlanningError("Plan checkpoint does not match the reviewed Plan")
        if freeze_worktree_subject(self.project_path).digest != expected_digest:
            raise PlanningError("Plan specification documents changed while approval was committed")

        def approve(current: ProjectRuntimeState) -> ProjectRuntimeState:
            self._assert_same_pending_plan(current, expected_digest)
            return replace(
                current,
                pending_action=None,
                pending_plan_subject_digest=None,
                current_plan_commit_sha=commit_sha,
                git_main_version=(
                    commit_sha
                    if current.rolling_started_at is not None
                    else current.git_main_version
                ),
            )

        task = ProjectOwnerTask(
            type=ProjectOwnerTaskType.PLAN_DECISION,
            content=_plan_decision_message(
                "approve",
                "",
                expected_digest,
                commit_sha,
            ),
        )
        updated, activation = self._apply_decision(
            task,
            reason=RuntimeContextChangeReason.PLAN_APPROVED,
            mutate=approve,
        )
        self.event_bus.publish(
            ExecutionEvent(
                triage_id=updated.triage_id,
                event_type=ExecutionEventType.PLAN_APPROVED,
                payload={"plan_commit_sha": commit_sha},
            )
        )
        return PlanDecision(state=updated, activation=activation, commit_sha=commit_sha)

    def reject_plan(self, feedback: str = "") -> PlanDecision:
        pending = self._assert_plan_pending()
        expected_digest = pending.pending_plan_subject_digest
        if expected_digest is None:
            raise PlanningError("Plan approval has no reviewed subject identity")

        def reject(current: ProjectRuntimeState) -> ProjectRuntimeState:
            self._assert_same_pending_plan(current, expected_digest)
            return replace(
                current,
                pending_action=None,
                pending_plan_subject_digest=None,
            )

        task = ProjectOwnerTask(
            type=ProjectOwnerTaskType.PLAN_DECISION,
            content=_plan_decision_message(
                "reject",
                feedback,
                expected_digest,
                pending.current_plan_commit_sha,
            ),
        )
        updated, activation = self._apply_decision(
            task,
            reason=RuntimeContextChangeReason.PLAN_REJECTED,
            mutate=reject,
        )
        self.event_bus.publish(
            ExecutionEvent(
                triage_id=updated.triage_id,
                event_type=ExecutionEventType.PLAN_REJECTED,
            )
        )
        return PlanDecision(state=updated, activation=activation)

    def _checkpoint_plan(
        self,
        state: ProjectRuntimeState,
        expected_digest: str,
    ) -> str:
        paths = plan_document_paths(self.project_path)
        changed_specs = set(self.git.changed_paths()).intersection(PLAN_DOCUMENT_NAMES)
        baseline = state.git_main_version if state.rolling_started_at is not None else None
        head = self.git.head_sha()

        if baseline is not None:
            if state.git_branch is None:
                raise PlanningError("Rolling delivery has no target Git branch")
            if self.git.current_branch() != state.git_branch:
                raise PlanningError("Project target branch changed during Plan approval")
            if head != baseline:
                if changed_specs or not self._is_reusable_checkpoint(baseline, head):
                    raise PlanningError("Project Git HEAD changed outside Plan approval")
                committed = freeze_commit_subject(self.project_path, self.git, head)
                if committed.digest != expected_digest:
                    raise PlanningError("Existing Plan checkpoint does not match the reviewed Plan")
                return head

        if not changed_specs:
            return head
        return self.git.commit_paths(paths, message=_PLAN_CHECKPOINT_MESSAGE)

    def _is_reusable_checkpoint(self, baseline: str, head: str) -> bool:
        if self.git.commit_parent(head) != baseline:
            return False
        changed = set(self.git.changed_paths_between(baseline, head))
        return bool(changed) and changed.issubset(PLAN_DOCUMENT_NAMES)

    def _apply_decision(
        self,
        task: ProjectOwnerTask,
        *,
        reason: RuntimeContextChangeReason,
        mutate: Callable[[ProjectRuntimeState], ProjectRuntimeState],
    ) -> tuple[ProjectRuntimeState, OwnerActivation]:
        with self.context.transaction() as transaction:
            updated = transaction.transition(reason=reason, mutate=mutate)
            activation = transaction.submit_owner_input(task)
        return updated, activation

    def _run_hard_gate(
        self,
        context: ProjectRuntimeState,
        subject: PlanSubject,
    ) -> PlanReviewResult:
        invocation_id = uuid4().hex
        self.event_bus.publish(
            ExecutionEvent(
                triage_id=context.triage_id,
                event_type=ExecutionEventType.AGENT_INVOCATION_STARTED,
                payload={
                    "invocation_id": invocation_id,
                    "operation": "plan_hard_gate",
                    "subject_digest": subject.digest,
                },
            )
        )
        try:
            review = self.review_plan(
                PlanReviewRequest(triage_id=context.triage_id, subject=subject)
            )
            self._validate_review(review, subject.digest)
        except Exception as error:
            self.event_bus.publish(
                ExecutionEvent(
                    triage_id=context.triage_id,
                    event_type=ExecutionEventType.AGENT_INVOCATION_FAILED,
                    payload={
                        "invocation_id": invocation_id,
                        "operation": "plan_hard_gate",
                        "subject_digest": subject.digest,
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
                    "operation": "plan_hard_gate",
                    "subject_digest": review.subject_digest,
                    "decision": review.decision,
                    "required_change_count": len(review.required_changes),
                    "review_artifact": {
                        "uri": review.audit_artifact.uri,
                        "project_relative_path": review.audit_artifact.project_relative_path,
                        "media_type": review.audit_artifact.media_type,
                        "size": review.audit_artifact.size,
                        "sha256": review.audit_artifact.sha256,
                    },
                },
            )
        )
        return review

    @staticmethod
    def _validate_review(review: PlanReviewResult, subject_digest: str) -> None:
        if review.subject_digest != subject_digest:
            raise PlanningError("Plan Hard Gate reviewed a different subject")
        if not review.summary.strip():
            raise PlanningError("Plan Hard Gate returned an empty summary")
        if review.decision == "pass" and review.required_changes:
            raise PlanningError("Plan Hard Gate pass must not contain required changes")
        if review.decision == "revise" and not review.required_changes:
            raise PlanningError("Plan Hard Gate revise must contain required changes")

    @staticmethod
    def _assert_requestable(context: ProjectRuntimeState) -> None:
        if context.pending_action is not None:
            raise PlanningError(f"Project already has a pending action: {context.pending_action}")
        if context.status not in {"TRIAGE", "TODO", "IN_PROGRESS", "BLOCKED"}:
            raise PlanningError(f"Plan approval cannot be requested from status {context.status}")

    def _assert_plan_pending(self) -> ProjectRuntimeState:
        current = self.context.state()
        self._assert_pending_action(current)
        return current

    @staticmethod
    def _assert_pending_action(context: ProjectRuntimeState) -> None:
        if context.pending_action != "PLAN_APPROVAL":
            raise PlanningError("Project is not waiting for Plan approval")

    @classmethod
    def _assert_same_pending_plan(
        cls,
        context: ProjectRuntimeState,
        expected_digest: str,
    ) -> None:
        cls._assert_pending_action(context)
        if context.pending_plan_subject_digest != expected_digest:
            raise PlanningError("Pending Plan changed while its decision was being applied")


def _plan_decision_message(
    action: Literal["approve", "reject"],
    feedback: str,
    subject_digest: str,
    commit_sha: str | None,
) -> str:
    approved = action == "approve"
    return json.dumps(
        {
            "event": "PLAN_DECISION_RECEIVED",
            "decision": "APPROVED" if approved else "REJECTED",
            "plan_subject_digest": subject_digest,
            "plan_commit_sha": commit_sha,
            "feedback": feedback.strip() or None,
            "required_response": (
                "Reconcile the complete Milestone View with the approved Plan, then "
                "request the first or next unfinished Milestone when delivery is ready."
                if approved
                else "Revise the canonical Specs with the user, then request approval "
                "again only when the complete Plan is ready."
            ),
        },
        ensure_ascii=False,
        indent=2,
    )
