"""Stable read model for headless and UI project-control clients."""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from agentplanex.domains import (
    ExecutionEvent,
    MessageHistory,
    MilestoneSnapshot,
    OwnerActivation,
    OwnerActivationStatus,
    ProjectOwnerTaskType,
    ProjectRuntimeContext,
    StageRun,
    StageRunStatus,
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
    """One safe, human-facing entry from the Owner conversation."""

    message_id: str
    role: Literal["user", "assistant", "status"]
    content: str


@dataclass(frozen=True, slots=True)
class PlanDocument:
    """One canonical planning document, if it exists yet."""

    name: str
    content: str | None


@dataclass(frozen=True, slots=True)
class ProjectControlView:
    """One composed projection over existing Runtime, Git, and Timeline facts."""

    context: ProjectRuntimeContext
    snapshot: MilestoneSnapshot | None
    stage_runs: tuple[StageRun, ...]
    owner_activation: OwnerActivation | None
    timeline: tuple[ExecutionEvent, ...]
    conversation: tuple[VisibleMessage, ...]
    plan_documents: tuple[PlanDocument, ...]
    plan_error: str | None
    git_branch: str | None
    git_head: str | None
    git_error: str | None
    allowed_actions: tuple[str, ...]


@dataclass(slots=True)
class ProjectControlQuery:
    """Build a view without making business decisions or writing state."""

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

    def __post_init__(self) -> None:
        if self.history_limit <= 0:
            raise ValueError("Project Control history limit must be positive")

    def get(self, triage_id: str) -> ProjectControlView:
        with self.database.connection() as connection:
            context = self.contexts.get(connection, triage_id)
            if context is None:
                raise LookupError(f"Project Runtime Context not found: {triage_id}")
            snapshot = (
                self.snapshots.get(connection, context.current_snapshot_id)
                if context.current_snapshot_id is not None
                else None
            )
            stage_runs = self.stage_runs.list_by_triage_id(connection, triage_id)
            activation = self.activations.get_unfinished(connection, triage_id)
            activation_history = self.activations.list_by_triage_id(
                connection, triage_id
            )
            timeline = self.events.list_by_triage_id(connection, triage_id)
            active_stage = self.stage_runs.get_active(connection, triage_id)
            owner = self.owners.get_by_triage_id(connection, triage_id)
            conversation = (
                _visible_messages(
                    self.messages.list_by_session_id(
                        connection, owner.project_owner_session_id
                    ),
                    activation_history,
                )
                if owner is not None
                else ()
            )
        try:
            branch = self.git.current_branch()
            head = self.git.head_sha()
            git_error = None
        except GitRepositoryError as error:
            branch = None
            head = None
            git_error = str(error)
        plan_documents, plan_error = _read_plan_documents(self.git.project_path)
        return ProjectControlView(
            context=context,
            snapshot=snapshot,
            stage_runs=stage_runs[-self.history_limit :],
            owner_activation=activation,
            timeline=timeline[-self.history_limit :],
            conversation=conversation,
            plan_documents=plan_documents,
            plan_error=plan_error,
            git_branch=branch,
            git_head=head,
            git_error=git_error,
            allowed_actions=_allowed_actions(context, activation, active_stage),
        )


def _visible_messages(
    histories: tuple[MessageHistory, ...],
    activations: tuple[OwnerActivation, ...],
) -> tuple[VisibleMessage, ...]:
    activation_by_message = {item.message_id: item for item in activations}
    visible: list[VisibleMessage] = []
    for history in histories:
        activation = activation_by_message.get(history.message_id)
        for index, message in enumerate(history.message):
            role = message.get("role")
            content = message.get("content")
            response_text = _assistant_response_text(message)
            if response_text:
                visible.append(
                    VisibleMessage(
                        message_id=f"{history.message_id}:{index}",
                        role="assistant",
                        content=response_text,
                    )
                )
                continue
            if not isinstance(content, str) or not content.strip():
                continue
            if role == "assistant":
                visible.append(
                    VisibleMessage(
                        message_id=f"{history.message_id}:{index}",
                        role="assistant",
                        content=content,
                    )
                )
            elif role == "user" and activation is not None:
                if activation.task_type is ProjectOwnerTaskType.USER_INPUT:
                    visible.append(
                        VisibleMessage(
                            message_id=f"{history.message_id}:{index}",
                            role="user",
                            content=content,
                        )
                    )
                elif activation.task_type is ProjectOwnerTaskType.PLAN_DECISION:
                    visible.append(
                        VisibleMessage(
                            message_id=f"{history.message_id}:{index}",
                            role="status",
                            content=_plan_decision_text(content),
                        )
                    )
    visible.extend(
        VisibleMessage(
            message_id=f"{activation.activation_id}:failure",
            role="status",
            content=f"Project Owner failed: {activation.failure}",
        )
        for activation in activations
        if activation.failure is not None
    )
    return tuple(visible)


def _assistant_response_text(message: dict[str, object]) -> str:
    if message.get("object") != "response":
        return ""
    parts: list[str] = []
    output = message.get("output")
    if not isinstance(output, list):
        return ""
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "output_text":
                continue
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


def _read_plan_documents(
    project_path: Path,
) -> tuple[tuple[PlanDocument, ...], str | None]:
    documents: list[PlanDocument] = []
    try:
        for name in SPEC_DOCUMENT_NAMES:
            path = project_path / name
            documents.append(
                PlanDocument(
                    name=name,
                    content=(
                        path.read_text(encoding="utf-8") if path.exists() else None
                    ),
                )
            )
    except OSError as error:
        return (), str(error)
    return tuple(documents), None


def _allowed_actions(
    context: ProjectRuntimeContext,
    activation: OwnerActivation | None,
    active_stage: StageRun | None,
) -> tuple[str, ...]:
    if activation is not None and activation.status in {
        OwnerActivationStatus.PENDING,
        OwnerActivationStatus.RUNNING,
    }:
        return ("drive",)
    if active_stage is not None and active_stage.status in {
        StageRunStatus.QUEUED,
        StageRunStatus.RUNNING,
    }:
        return ("drive-delivery",)
    actions = ["message"]
    if context.pending_action == "PLAN_APPROVAL":
        actions.extend(("approve", "reject"))
    elif context.pending_action == "FIRST_RUN_APPROVAL":
        actions.append("start")
    return tuple(actions)
