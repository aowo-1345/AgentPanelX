"""Plan approval workflow over project Specs, Git, and Runtime state."""

import hashlib
import sqlite3
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
    ProjectOwnerTaskType,
    ProjectRuntimeContext,
    RuntimeContextChangeReason,
)
from agentplanex.infrastructure.git_repository import GitRepository
from agentplanex.infrastructure.sqlite import (
    SQLiteDatabase,
    initialize_schema,
)
from agentplanex.infrastructure.sqlite.repositories import (
    SQLiteOwnerActivationRepository,
    SQLiteProjectRuntimeContextRepository,
)
from agentplanex.infrastructure.sqlite.timeline import SQLiteTimelineRecorder
from agentplanex.services.event_bus import EventBus
from agentplanex.services.runtime_context import RuntimeContextService

SPEC_DOCUMENT_NAMES = ("architecture.md", "requirements.md", "roadmap.md")
type PlanDecisionMessageWriter = Callable[[sqlite3.Connection], str]


class PlanningError(ValueError):
    """An expected planning error that the Project Owner can correct."""


@dataclass(frozen=True, slots=True)
class PlanReviewRequest:
    """The exact Plan subject supplied to a protected external review."""

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
    context: ProjectRuntimeContext
    activation: OwnerActivation
    commit_sha: str | None = None


@dataclass(frozen=True, slots=True)
class PlanApprovalRequest:
    """The observable result of one Plan Hard Gate invocation."""

    context: ProjectRuntimeContext
    accepted: bool
    review: PlanReviewResult


@dataclass(slots=True)
class PlanningService:
    project_path: Path
    database: SQLiteDatabase
    contexts: SQLiteProjectRuntimeContextRepository = field(
        default_factory=SQLiteProjectRuntimeContextRepository
    )
    activations: SQLiteOwnerActivationRepository = field(
        default_factory=SQLiteOwnerActivationRepository
    )
    git: GitRepository | None = None
    review_plan: PlanHardGate = missing_plan_hard_gate
    event_bus: EventBus = field(default_factory=EventBus)
    runtime_contexts: RuntimeContextService | None = None

    def __post_init__(self) -> None:
        if self.git is None:
            self.git = GitRepository(self.project_path)
        if self.runtime_contexts is None:
            self.runtime_contexts = RuntimeContextService(
                self.database,
                self.event_bus,
                self.contexts,
            )

    @classmethod
    def for_project(cls, project_path: Path) -> "PlanningService":
        database = SQLiteDatabase.for_project(project_path)
        initialize_schema(database)
        event_bus = EventBus((SQLiteTimelineRecorder(database),))
        return cls(
            project_path=project_path,
            database=database,
            event_bus=event_bus,
            runtime_contexts=RuntimeContextService(database, event_bus),
        )

    def request_plan_approval(
        self,
        context: ProjectRuntimeContext,
    ) -> PlanApprovalRequest:
        before = self._current_context(context.triage_id)
        self._assert_requestable(before)
        spec_documents = self._spec_documents()
        subject_digest = self._subject_digest(spec_documents)
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
                        "media_type": review.audit_artifact.media_type,
                        "size": review.audit_artifact.size,
                        "sha256": review.audit_artifact.sha256,
                    },
                },
            )
        )

        after = self._current_context(context.triage_id)
        self._assert_requestable(after)
        if self._subject_digest(spec_documents) != subject_digest:
            raise PlanningError(
                "Plan specification documents changed during Hard Gate review"
            )
        if review.decision == "revise":
            return PlanApprovalRequest(context=after, accepted=False, review=review)

        runtime_contexts = self._runtime_contexts()

        def request(current: ProjectRuntimeContext) -> ProjectRuntimeContext:
            self._assert_requestable(current)

            status = "BLOCKED" if current.status == "IN_PROGRESS" else "TODO"
            updated = replace(
                current,
                status=status,
                pending_action="PLAN_APPROVAL",
                pending_plan_subject_digest=subject_digest,
            )
            return updated

        updated = runtime_contexts.transition(
            context.triage_id,
            reason=RuntimeContextChangeReason.PLAN_APPROVAL_REQUESTED,
            mutate=request,
        )
        self.event_bus.publish(
            ExecutionEvent(
                triage_id=context.triage_id,
                event_type=ExecutionEventType.PLAN_APPROVAL_REQUESTED,
            )
        )
        return PlanApprovalRequest(context=updated, accepted=True, review=review)

    def approve_plan(
        self,
        triage_id: str,
        *,
        append_message: PlanDecisionMessageWriter,
    ) -> PlanDecision:
        spec_documents = self._spec_documents()
        pending = self._assert_plan_pending(triage_id)
        expected_digest = pending.pending_plan_subject_digest
        if expected_digest is None:
            raise PlanningError("Plan approval has no reviewed subject identity")
        if self._subject_digest(spec_documents) != expected_digest:
            raise PlanningError(
                "Plan specification documents changed after Hard Gate review"
            )
        git = self.git
        if git is None:
            raise RuntimeError("Planning Service has no Git repository")
        commit_sha = git.commit_paths(
            spec_documents,
            message="plan: approve specifications",
        )

        def approve(current: ProjectRuntimeContext) -> ProjectRuntimeContext:
            self._assert_pending_action(current)
            return replace(
                current,
                status=(
                    "IN_PROGRESS"
                    if current.rolling_started_at is not None
                    else "TODO"
                ),
                pending_action=None,
                pending_plan_subject_digest=None,
                current_plan_commit_sha=commit_sha,
            )

        updated, activation = self._apply_decision(
            triage_id,
            append_message=append_message,
            reason=RuntimeContextChangeReason.PLAN_APPROVED,
            mutate=approve,
        )
        self.event_bus.publish(
            ExecutionEvent(
                triage_id=triage_id,
                event_type=ExecutionEventType.PLAN_APPROVED,
                payload={"plan_commit_sha": commit_sha},
            )
        )

        return PlanDecision(
            context=updated,
            activation=activation,
            commit_sha=commit_sha,
        )

    def reject_plan(
        self,
        triage_id: str,
        *,
        append_message: PlanDecisionMessageWriter,
    ) -> PlanDecision:
        def reject(current: ProjectRuntimeContext) -> ProjectRuntimeContext:
            self._assert_pending_action(current)
            return replace(
                current,
                status=(
                    "IN_PROGRESS"
                    if current.rolling_started_at is not None
                    else "TODO"
                ),
                pending_action=None,
                pending_plan_subject_digest=None,
            )

        updated, activation = self._apply_decision(
            triage_id,
            append_message=append_message,
            reason=RuntimeContextChangeReason.PLAN_REJECTED,
            mutate=reject,
        )
        self.event_bus.publish(
            ExecutionEvent(
                triage_id=triage_id,
                event_type=ExecutionEventType.PLAN_REJECTED,
            )
        )

        return PlanDecision(context=updated, activation=activation)

    def _apply_decision(
        self,
        triage_id: str,
        *,
        append_message: PlanDecisionMessageWriter,
        reason: RuntimeContextChangeReason,
        mutate: Callable[[ProjectRuntimeContext], ProjectRuntimeContext],
    ) -> tuple[ProjectRuntimeContext, OwnerActivation]:
        with self.database.transaction() as connection:
            updated, context_event = self._runtime_contexts().transition_in_transaction(
                connection,
                triage_id,
                reason=reason,
                mutate=mutate,
            )
            message_id = append_message(connection)
            activation = OwnerActivation(
                activation_id=uuid4().hex,
                triage_id=triage_id,
                task_type=ProjectOwnerTaskType.PLAN_DECISION,
                message_id=message_id,
            )
            self.activations.insert(connection, activation)
        if context_event is not None:
            self.event_bus.publish(context_event)
        return updated, activation

    def _spec_documents(self) -> tuple[Path, ...]:
        paths = tuple(self.project_path / name for name in SPEC_DOCUMENT_NAMES)
        missing = tuple(path.name for path in paths if not path.is_file())
        if missing:
            raise PlanningError(
                "Missing Plan specification documents: " + ", ".join(missing)
            )
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

    def _current_context(self, triage_id: str) -> ProjectRuntimeContext:
        with self.database.connection() as connection:
            return self._get_context(connection, triage_id)

    @staticmethod
    def _assert_requestable(context: ProjectRuntimeContext) -> None:
        if context.pending_action is not None:
            raise PlanningError(
                "Project already has a pending action: " f"{context.pending_action}"
            )
        if context.status not in {"TRIAGE", "TODO", "IN_PROGRESS"}:
            raise PlanningError(
                "Plan approval cannot be requested from status " f"{context.status}"
            )

    def _assert_plan_pending(self, triage_id: str) -> ProjectRuntimeContext:
        with self.database.connection() as connection:
            current = self._get_context(connection, triage_id)
        self._assert_pending_action(current)
        return current

    @staticmethod
    def _assert_pending_action(context: ProjectRuntimeContext) -> None:
        if context.pending_action != "PLAN_APPROVAL":
            raise PlanningError("Project is not waiting for Plan approval")

    def _get_context(
        self,
        connection: sqlite3.Connection,
        triage_id: str,
    ) -> ProjectRuntimeContext:
        context = self.contexts.get(connection, triage_id)
        if context is None:
            raise LookupError(f"Project Runtime Context not found: {triage_id}")
        return context

    def _runtime_contexts(self) -> RuntimeContextService:
        if self.runtime_contexts is None:
            raise RuntimeError("Planning Service has no Runtime Context Service")
        return self.runtime_contexts
