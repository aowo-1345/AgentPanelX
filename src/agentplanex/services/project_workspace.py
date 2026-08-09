"""Web-ready projection with independently degradable Feature panels."""

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Literal

from agentplanex.domains import (
    ExecutionEvent,
    FeatureAction,
    Message,
    MessageHistory,
    MilestoneSnapshot,
    OwnerActivation,
    ProjectOwnerTaskType,
    ProjectRuntimeContext,
    StageRun,
)
from agentplanex.infrastructure.git_repository import GitRepository, GitRepositoryError
from agentplanex.infrastructure.sqlite import SQLiteDatabase
from agentplanex.infrastructure.sqlite.repositories import (
    SQLiteExecutionEventRepository,
    SQLiteMessageHistoryRepository,
    SQLiteMilestoneSnapshotRepository,
    SQLiteOwnerActivationRepository,
    SQLiteProjectOwnerAgentRepository,
    SQLiteProjectRuntimeContextRepository,
    SQLiteStageRunRepository,
)
from agentplanex.services.planning import SPEC_DOCUMENT_NAMES


@dataclass(frozen=True, slots=True)
class VisibleMessage:
    message_id: str
    role: Literal["user", "assistant", "status"]
    content: str


@dataclass(frozen=True, slots=True)
class PlanDocument:
    name: str
    content: str | None


@dataclass(frozen=True, slots=True)
class ProjectWorkspaceView:
    """Panels derived from one required Runtime Context."""

    context: ProjectRuntimeContext
    owner_activation: OwnerActivation | None
    runtime_error: str | None
    snapshot: MilestoneSnapshot | None
    milestones_error: str | None
    timeline: tuple[ExecutionEvent, ...]
    timeline_error: str | None
    conversation: tuple[VisibleMessage, ...]
    conversation_error: str | None
    plan_documents: tuple[PlanDocument, ...]
    plan_error: str | None
    git_branch: str | None
    git_head: str | None
    git_error: str | None
    available_actions: tuple[FeatureAction, ...]


@dataclass(slots=True)
class ProjectWorkspaceQuery:
    """Compose UI panels without weakening the existing control query."""

    database: SQLiteDatabase
    git: GitRepository
    contexts: SQLiteProjectRuntimeContextRepository = field(
        default_factory=SQLiteProjectRuntimeContextRepository
    )
    snapshots: SQLiteMilestoneSnapshotRepository = field(
        default_factory=SQLiteMilestoneSnapshotRepository
    )
    stage_runs: SQLiteStageRunRepository = field(
        default_factory=SQLiteStageRunRepository
    )
    activations: SQLiteOwnerActivationRepository = field(
        default_factory=SQLiteOwnerActivationRepository
    )
    owners: SQLiteProjectOwnerAgentRepository = field(
        default_factory=SQLiteProjectOwnerAgentRepository
    )
    messages: SQLiteMessageHistoryRepository = field(
        default_factory=SQLiteMessageHistoryRepository
    )
    events: SQLiteExecutionEventRepository = field(
        default_factory=SQLiteExecutionEventRepository
    )
    history_limit: int = 50

    def get(self, triage_id: str) -> ProjectWorkspaceView:
        context = self._context(triage_id)
        activation, active_stage, runtime_error = self._runtime(triage_id)
        snapshot, milestones_error = self._milestones(context)
        timeline, timeline_error = self._timeline(triage_id)
        conversation, conversation_error = self._conversation(triage_id)
        plan_documents, plan_error = _read_plan_documents(self.git)
        branch, head, git_error = _git_panel(self.git)
        return ProjectWorkspaceView(
            context=context,
            owner_activation=activation,
            runtime_error=runtime_error,
            snapshot=snapshot,
            milestones_error=milestones_error,
            timeline=timeline,
            timeline_error=timeline_error,
            conversation=conversation,
            conversation_error=conversation_error,
            plan_documents=plan_documents,
            plan_error=plan_error,
            git_branch=branch,
            git_head=head,
            git_error=git_error,
            available_actions=_human_actions(
                context,
                activation,
                active_stage,
                runtime_error,
            ),
        )

    def _context(self, triage_id: str) -> ProjectRuntimeContext:
        with self.database.connection() as connection:
            context = self.contexts.get(connection, triage_id)
        if context is None:
            raise LookupError(f"Project Runtime Context not found: {triage_id}")
        return context

    def _runtime(
        self,
        triage_id: str,
    ) -> tuple[OwnerActivation | None, StageRun | None, str | None]:
        try:
            with self.database.connection() as connection:
                return (
                    self.activations.get_unfinished(connection, triage_id),
                    self.stage_runs.get_active(connection, triage_id),
                    None,
                )
        except (sqlite3.Error, ValueError) as error:
            return None, None, str(error)

    def _milestones(
        self,
        context: ProjectRuntimeContext,
    ) -> tuple[MilestoneSnapshot | None, str | None]:
        if context.current_snapshot_id is None:
            return None, None
        try:
            with self.database.connection() as connection:
                snapshot = self.snapshots.get(connection, context.current_snapshot_id)
            if snapshot is None:
                raise LookupError(
                    f"Milestone Snapshot not found: {context.current_snapshot_id}"
                )
            return snapshot, None
        except (sqlite3.Error, ValueError, LookupError) as error:
            return None, str(error)

    def _timeline(
        self,
        triage_id: str,
    ) -> tuple[tuple[ExecutionEvent, ...], str | None]:
        try:
            with self.database.connection() as connection:
                events = self.events.list_by_triage_id(connection, triage_id)
            return events[-self.history_limit :], None
        except (sqlite3.Error, ValueError) as error:
            return (), str(error)

    def _conversation(
        self,
        triage_id: str,
    ) -> tuple[tuple[VisibleMessage, ...], str | None]:
        try:
            with self.database.connection() as connection:
                owner = self.owners.get_by_triage_id(connection, triage_id)
                if owner is None:
                    return (), None
                histories = self.messages.list_by_session_id(
                    connection, owner.project_owner_session_id
                )
                activations = self.activations.list_by_triage_id(
                    connection, triage_id
                )
            return _visible_messages(histories, activations), None
        except (sqlite3.Error, ValueError) as error:
            return (), str(error)


def _human_actions(
    context: ProjectRuntimeContext,
    activation: OwnerActivation | None,
    active_stage: StageRun | None,
    runtime_error: str | None,
) -> tuple[FeatureAction, ...]:
    if runtime_error is not None or activation is not None or active_stage is not None:
        return ()
    if context.status == "TRIAGE":
        return (FeatureAction.BEGIN,)
    if context.pending_action == "PLAN_APPROVAL":
        return (FeatureAction.APPROVE_PLAN, FeatureAction.REJECT_PLAN)
    if context.pending_action == "FIRST_RUN_APPROVAL":
        return (FeatureAction.START_DELIVERY,)
    return ()


def _git_panel(git: GitRepository) -> tuple[str | None, str | None, str | None]:
    try:
        return git.current_branch(), git.head_sha(), None
    except GitRepositoryError as error:
        return None, None, str(error)


def _read_plan_documents(
    git: GitRepository,
) -> tuple[tuple[PlanDocument, ...], str | None]:
    try:
        return (
            tuple(
                PlanDocument(
                    name=name,
                    content=(
                        (git.project_path / name).read_text(encoding="utf-8")
                        if (git.project_path / name).exists()
                        else None
                    ),
                )
                for name in SPEC_DOCUMENT_NAMES
            ),
            None,
        )
    except OSError as error:
        return (), str(error)


def _visible_messages(
    histories: tuple[MessageHistory, ...],
    activations: tuple[OwnerActivation, ...],
) -> tuple[VisibleMessage, ...]:
    activation_by_message = {item.message_id: item for item in activations}
    visible: list[VisibleMessage] = []
    for history in histories:
        activation = activation_by_message.get(history.message_id)
        for index, message in enumerate(history.message):
            response_text = _assistant_response_text(message)
            if response_text:
                visible.append(
                    VisibleMessage(
                        f"{history.message_id}:{index}", "assistant", response_text
                    )
                )
                continue
            role = message.get("role")
            content = message.get("content")
            if not isinstance(content, str) or not content.strip():
                continue
            if role == "assistant":
                visible.append(
                    VisibleMessage(f"{history.message_id}:{index}", "assistant", content)
                )
            elif role == "user" and activation is not None:
                if activation.task_type is ProjectOwnerTaskType.USER_INPUT:
                    visible.append(
                        VisibleMessage(f"{history.message_id}:{index}", "user", content)
                    )
                elif activation.task_type is ProjectOwnerTaskType.PLAN_DECISION:
                    visible.append(
                        VisibleMessage(
                            f"{history.message_id}:{index}",
                            "status",
                            _plan_decision_text(content),
                        )
                    )
    visible.extend(
        VisibleMessage(
            f"{activation.activation_id}:failure",
            "status",
            f"Project Owner failed: {activation.failure}",
        )
        for activation in activations
        if activation.failure is not None
    )
    return tuple(visible)


def _assistant_response_text(message: Message) -> str:
    if message.get("object") != "response":
        return ""
    output = message.get("output")
    if not isinstance(output, list):
        return ""
    parts: list[str] = []
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, dict) and part.get("type") == "output_text":
                text = part.get("text")
                if isinstance(text, str) and text:
                    parts.append(text)
    return "\n".join(parts).strip()


def _plan_decision_text(content: str) -> str:
    try:
        decision = json.loads(content)
    except json.JSONDecodeError:
        return "Plan decision recorded."
    if not isinstance(decision, dict):
        return "Plan decision recorded."
    label = str(decision.get("decision", "recorded")).lower()
    feedback = decision.get("feedback")
    return f"Plan {label}." + (f" Feedback: {feedback}" if feedback else "")
