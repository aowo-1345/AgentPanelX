"""Plan approval workflow over project Specs, Git, and Runtime state."""

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal
from uuid import uuid4

from agentplanex.domains import (
    ArtifactDescriptor,
    ExecutionEvent,
    ExecutionEventType,
    OwnerActivation,
    ProjectOwnerTask,
    ProjectOwnerTaskType,
    ProjectRuntimeState,
    RuntimeContextChangeReason,
)
from agentplanex.infrastructure.git_repository import GitRepository
from agentplanex.services.event_bus import EventBus
from agentplanex.services.project_runtime_context import ProjectRuntimeContext

SPEC_DOCUMENT_NAMES = ("architecture.md", "requirements.md", "roadmap.md")


class PlanningError(ValueError):
    """An expected planning error that the Project Owner can correct."""


@dataclass(frozen=True, slots=True)
class PlanReviewRequest:
    """The exact Plan subject supplied to a protected external review."""

    triage_id: str
    spec_documents: tuple[Path, ...]
    subject_digest: str


@dataclass(frozen=True, slots=True)
class PlanReviewResult:
    """The validated result required from the Plan Hard Gate Contract."""

    subject_digest: str
    decision: Literal["pass", "revise"]
    summary: str
    required_changes: tuple[str, ...]
    audit_artifact: ArtifactDescriptor


type PlanHardGate = Callable[[PlanReviewRequest], PlanReviewResult]


def missing_plan_hard_gate(_request: PlanReviewRequest) -> PlanReviewResult:
    """Fail closed when a Planning Service has no configured gate."""
    raise PlanningError("Plan Hard Gate is not configured")


@dataclass(frozen=True, slots=True)
class PlanDecision:
    state: ProjectRuntimeState
    activation: OwnerActivation
    commit_sha: str | None = None


@dataclass(frozen=True, slots=True)
class PlanApprovalRequest:
    """The observable result of submitting one exact Plan for human approval."""

    state: ProjectRuntimeState
    accepted: bool
    subject_digest: str
    review: PlanReviewResult | None


@dataclass(slots=True)
class PlanningService:
    project_path: Path
    context: ProjectRuntimeContext
    git: GitRepository
    review_plan: PlanHardGate = missing_plan_hard_gate
    event_bus: EventBus = field(default_factory=EventBus)

    def request_plan_approval(self) -> PlanApprovalRequest:
        before = self.context.state()
        self._assert_requestable(before)
        spec_documents = self._spec_documents()
        subject_digest = self._subject_digest(spec_documents)
        review = (
            self._run_hard_gate(before, spec_documents, subject_digest)
            if before.status == "IN_PROGRESS"
            else None
        )

        after = self.context.state()
        self._assert_requestable(after)
        if self._subject_digest(spec_documents) != subject_digest:
            raise PlanningError("Plan specification documents changed while requesting approval")
        if review is not None and review.decision == "revise":
            return PlanApprovalRequest(
                state=after,
                accepted=False,
                subject_digest=subject_digest,
                review=review,
            )

        def request(current: ProjectRuntimeState) -> ProjectRuntimeState:
            self._assert_requestable(current)

            updated = replace(
                current,
                status=("TODO" if current.status == "TRIAGE" else current.status),
                pending_action="PLAN_APPROVAL",
                pending_plan_subject_digest=subject_digest,
            )
            return updated

        updated = self.context.transition(
            reason=RuntimeContextChangeReason.PLAN_APPROVAL_REQUESTED,
            mutate=request,
        )
        self.event_bus.publish(
            ExecutionEvent(
                triage_id=updated.triage_id,
                event_type=ExecutionEventType.PLAN_APPROVAL_REQUESTED,
                payload={
                    "subject_digest": subject_digest,
                    "hard_gate_invoked": review is not None,
                },
            )
        )
        return PlanApprovalRequest(
            state=updated,
            accepted=True,
            subject_digest=subject_digest,
            review=review,
        )

    def approve_plan(self) -> PlanDecision:
        spec_documents = self._spec_documents()
        pending = self._assert_plan_pending()
        expected_digest = pending.pending_plan_subject_digest
        if expected_digest is None:
            raise PlanningError("Plan approval has no reviewed subject identity")
        if self._subject_digest(spec_documents) != expected_digest:
            raise PlanningError("Plan specification documents changed after approval was requested")
        commit_sha = self.git.commit_paths(
            spec_documents,
            message="plan: approve specifications",
        )

        def approve(current: ProjectRuntimeState) -> ProjectRuntimeState:
            self._assert_pending_action(current)
            return replace(
                current,
                pending_action=None,
                pending_plan_subject_digest=None,
                current_plan_commit_sha=commit_sha,
            )

        task = ProjectOwnerTask(
            type=ProjectOwnerTaskType.PLAN_DECISION,
            content=_plan_decision_message("approve", "", expected_digest),
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

        return PlanDecision(
            state=updated,
            activation=activation,
            commit_sha=commit_sha,
        )

    def reject_plan(self, feedback: str = "") -> PlanDecision:
        pending = self._assert_plan_pending()

        def reject(current: ProjectRuntimeState) -> ProjectRuntimeState:
            self._assert_pending_action(current)
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
                pending.pending_plan_subject_digest,
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

    def _apply_decision(
        self,
        task: ProjectOwnerTask,
        *,
        reason: RuntimeContextChangeReason,
        mutate: Callable[[ProjectRuntimeState], ProjectRuntimeState],
    ) -> tuple[ProjectRuntimeState, OwnerActivation]:
        with self.context.transaction() as transaction:
            updated = transaction.transition(
                reason=reason,
                mutate=mutate,
            )
            activation = transaction.submit_owner_input(task)
        return updated, activation

    def _spec_documents(self) -> tuple[Path, ...]:
        paths = tuple(self.project_path / name for name in SPEC_DOCUMENT_NAMES)
        missing = tuple(path.name for path in paths if not path.is_file())
        if missing:
            raise PlanningError("Missing Plan specification documents: " + ", ".join(missing))
        return paths

    @staticmethod
    def _subject_digest(spec_documents: tuple[Path, ...]) -> str:
        digest = hashlib.sha256()
        for document in spec_documents:
            try:
                content = document.read_bytes()
            except OSError as error:
                raise PlanningError(
                    f"Cannot read Plan specification document: {document.name}"
                ) from error
            name = document.name.encode("utf-8")
            digest.update(len(name).to_bytes(4, "big"))
            digest.update(name)
            digest.update(len(content).to_bytes(8, "big"))
            digest.update(content)
        return digest.hexdigest()

    def _run_hard_gate(
        self,
        context: ProjectRuntimeState,
        spec_documents: tuple[Path, ...],
        subject_digest: str,
    ) -> PlanReviewResult:
        invocation_id = uuid4().hex
        self.event_bus.publish(
            ExecutionEvent(
                triage_id=context.triage_id,
                event_type=ExecutionEventType.AGENT_INVOCATION_STARTED,
                payload={
                    "invocation_id": invocation_id,
                    "operation": "plan_hard_gate",
                    "subject_digest": subject_digest,
                },
            )
        )
        try:
            review = self.review_plan(
                PlanReviewRequest(
                    triage_id=context.triage_id,
                    spec_documents=spec_documents,
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
                        "operation": "plan_hard_gate",
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
                    "operation": "plan_hard_gate",
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


def _plan_decision_message(
    action: Literal["approve", "reject"],
    feedback: str,
    subject_digest: str | None,
) -> str:
    approved = action == "approve"
    return json.dumps(
        {
            "event": "PLAN_DECISION_RECEIVED",
            "decision": "APPROVED" if approved else "REJECTED",
            "plan_subject_digest": subject_digest,
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
