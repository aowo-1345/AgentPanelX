"""Web-ready projection with independently degradable Feature panels."""

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from agentplanex.domains.execution_event import (
    ExecutionEvent,
)
from agentplanex.domains.project_runtime_state import ProjectRuntimeState
from agentplanex.domains.workspace import FeatureAction
from agentplanex.infrastructure.agent_workspace import AgentWorkspaceError, AgentWorkspaceStore
from agentplanex.infrastructure.git_repository import GitRepository, GitRepositoryError
from agentplanex.infrastructure.sqlite import SQLiteDatabase
from agentplanex.infrastructure.sqlite.repositories import (
    SQLiteAutoTakeoverRepository,
    SQLiteExecutionEventRepository,
    SQLiteMessageHistoryRepository,
    SQLiteMilestoneSnapshotRepository,
    SQLiteOwnerActivationRepository,
    SQLiteProjectOwnerAgentRepository,
    SQLiteProjectRuntimeStateRepository,
    SQLiteStageRunRepository,
)
from agentplanex.project_owner_agent.context.models import MessageHistory
from agentplanex.project_owner_agent.contracts import Message
from agentplanex.services.auto_takeover.models import TakeoverStatus
from agentplanex.services.delivery.models import MilestoneSnapshot, StageRun, StageRunStatus
from agentplanex.services.planning.models import PLAN_DOCUMENT_NAMES
from agentplanex.services.project_runtime_context.models import (
    OwnerActivation,
    OwnerActivationStatus,
)
from agentplanex.services.web.to_issue import CreatedIssue

type ToolActivityStatus = Literal["running", "completed", "failed"]
type AttributionState = Literal["idle", "running", "completed", "failed"]
type AttributionReportStatus = Literal["available", "unavailable"]


def _created_issue(number: int | None, url: str | None) -> CreatedIssue | None:
    if number is None or url is None:
        return None
    return CreatedIssue(number=number, url=url)


@dataclass(frozen=True, slots=True)
class ToolActivity:
    name: str
    status: ToolActivityStatus
    input_preview: str
    output_preview: str | None = None


@dataclass(frozen=True, slots=True)
class VisibleMessage:
    message_id: str
    role: Literal["user", "assistant", "status", "tool"]
    content: str
    tool_activity: ToolActivity | None = None


@dataclass(frozen=True, slots=True)
class ConversationSnapshot:
    """One complete conversation read used to seed an SSE subscriber."""

    histories: tuple[MessageHistory, ...]
    activations: tuple[OwnerActivation, ...]
    sequence: int


@dataclass(frozen=True, slots=True)
class PlanDocument:
    name: str
    content: str | None


@dataclass(frozen=True, slots=True)
class AttributionReport:
    run_id: str
    trigger_event_id: int
    created_at: datetime
    completed_at: datetime | None
    status: AttributionReportStatus
    content_markdown: str | None
    created_issue: CreatedIssue | None


@dataclass(frozen=True, slots=True)
class AttributionData:
    state: AttributionState
    reports: tuple[AttributionReport, ...]


@dataclass(frozen=True, slots=True)
class ProjectWorkspaceView:
    """Panels derived from one required persisted Runtime State."""

    state: ProjectRuntimeState
    owner_activation: OwnerActivation | None
    activation_has_reply: bool
    runtime_error: str | None
    snapshot: MilestoneSnapshot | None
    milestones_error: str | None
    timeline: tuple[ExecutionEvent, ...]
    timeline_error: str | None
    conversation: tuple[VisibleMessage, ...] | None
    conversation_error: str | None
    plan_documents: tuple[PlanDocument, ...]
    plan_error: str | None
    git_branch: str | None
    git_head: str | None
    git_error: str | None
    available_actions: tuple[FeatureAction, ...]
    active_stage_run_id: str | None = None
    active_stage_run_status: StageRunStatus | None = None
    attribution: AttributionData = field(
        default_factory=lambda: AttributionData(state="idle", reports=())
    )
    attribution_error: str | None = None


@dataclass(slots=True)
class ProjectWorkspaceQuery:
    """Compose UI panels without weakening the existing control query."""

    database: SQLiteDatabase
    git: GitRepository
    artifacts: AgentWorkspaceStore
    states: SQLiteProjectRuntimeStateRepository = field(
        default_factory=SQLiteProjectRuntimeStateRepository
    )
    snapshots: SQLiteMilestoneSnapshotRepository = field(
        default_factory=SQLiteMilestoneSnapshotRepository
    )
    stage_runs: SQLiteStageRunRepository = field(default_factory=SQLiteStageRunRepository)
    activations: SQLiteOwnerActivationRepository = field(
        default_factory=SQLiteOwnerActivationRepository
    )
    owners: SQLiteProjectOwnerAgentRepository = field(
        default_factory=SQLiteProjectOwnerAgentRepository
    )
    messages: SQLiteMessageHistoryRepository = field(default_factory=SQLiteMessageHistoryRepository)
    events: SQLiteExecutionEventRepository = field(default_factory=SQLiteExecutionEventRepository)
    takeover_runs: SQLiteAutoTakeoverRepository = field(
        default_factory=SQLiteAutoTakeoverRepository
    )
    history_limit: int = 50
    attribution_history_limit: int = 20

    def conversation_snapshot(self, triage_id: str) -> ConversationSnapshot:
        with self.database.read_only_connection() as connection:
            owner = self.owners.get_by_triage_id(connection, triage_id)
            if owner is None:
                return ConversationSnapshot((), (), 0)
            histories = self.messages.list_by_session_id(
                connection,
                owner.project_owner_session_id,
            )
            activations = self.activations.list_by_triage_id(connection, triage_id)
        return ConversationSnapshot(
            histories=histories,
            activations=activations,
            sequence=max((item.sequence for item in histories), default=0),
        )

    def get(self, triage_id: str, *, include_conversation: bool = True) -> ProjectWorkspaceView:
        state = self._state(triage_id)
        activation, active_stage, runtime_error = self._runtime(triage_id)
        snapshot, milestones_error = self._milestones(state)
        timeline, timeline_error = self._timeline(triage_id)
        if include_conversation:
            conversation, conversation_error, activation_has_reply = self._conversation(
                triage_id,
                activation,
            )
        else:
            conversation, conversation_error, activation_has_reply = None, None, False
        plan_documents, plan_error = _read_plan_documents(self.git)
        attribution, attribution_error = self._attribution(triage_id)
        branch, head, git_error = _git_panel(self.git)
        return ProjectWorkspaceView(
            state=state,
            active_stage_run_id=(active_stage.stage_run_id if active_stage is not None else None),
            active_stage_run_status=(active_stage.status if active_stage is not None else None),
            owner_activation=activation,
            activation_has_reply=activation_has_reply,
            runtime_error=runtime_error,
            snapshot=snapshot,
            milestones_error=milestones_error,
            timeline=timeline,
            timeline_error=timeline_error,
            conversation=conversation,
            conversation_error=conversation_error,
            plan_documents=plan_documents,
            plan_error=plan_error,
            attribution=attribution,
            attribution_error=attribution_error,
            git_branch=branch,
            git_head=head,
            git_error=git_error,
            available_actions=_human_actions(
                state,
                activation,
                active_stage,
                runtime_error,
            ),
        )

    def _attribution(self, triage_id: str) -> tuple[AttributionData, str | None]:
        try:
            with self.database.read_only_connection() as connection:
                latest = self.takeover_runs.latest(connection, triage_id)
                runs = self.takeover_runs.list_reports(
                    connection,
                    triage_id,
                    limit=self.attribution_history_limit,
                )
        except (sqlite3.Error, ValueError) as error:
            return AttributionData(state="idle", reports=()), str(error)

        reports: list[AttributionReport] = []
        for run in runs:
            if run.attribution is None:
                continue
            try:
                content = self.artifacts.read_descriptor_text(run.attribution)
            except AgentWorkspaceError:
                reports.append(
                    AttributionReport(
                        run_id=run.run_id,
                        trigger_event_id=run.trigger_event_id,
                        created_at=run.started_at,
                        completed_at=run.finished_at,
                        status="unavailable",
                        content_markdown=None,
                        created_issue=_created_issue(run.issue_number, run.issue_url),
                    )
                )
            else:
                reports.append(
                    AttributionReport(
                        run_id=run.run_id,
                        trigger_event_id=run.trigger_event_id,
                        created_at=run.started_at,
                        completed_at=run.finished_at,
                        status="available",
                        content_markdown=content,
                        created_issue=_created_issue(run.issue_number, run.issue_url),
                    )
                )

        if latest is not None and latest.status is TakeoverStatus.RUNNING:
            state: AttributionState = "running"
        elif latest is not None and latest.status is TakeoverStatus.FAILED:
            state = "failed"
        elif latest is not None and latest.attribution is not None:
            state = "completed"
        else:
            state = "idle"
        return AttributionData(state=state, reports=tuple(reports)), None

    def _state(self, triage_id: str) -> ProjectRuntimeState:
        with self.database.read_only_connection() as connection:
            state = self.states.get(connection, triage_id)
        if state is None:
            raise LookupError(f"Project Runtime State not found: {triage_id}")
        return state

    def _runtime(
        self,
        triage_id: str,
    ) -> tuple[OwnerActivation | None, StageRun | None, str | None]:
        try:
            with self.database.read_only_connection() as connection:
                return (
                    self.activations.get_unfinished(connection, triage_id),
                    self.stage_runs.get_active(connection, triage_id),
                    None,
                )
        except (sqlite3.Error, ValueError) as error:
            return None, None, str(error)

    def _milestones(
        self,
        state: ProjectRuntimeState,
    ) -> tuple[MilestoneSnapshot | None, str | None]:
        if state.current_snapshot_id is None:
            return None, None
        try:
            with self.database.read_only_connection() as connection:
                snapshot = self.snapshots.get(connection, state.current_snapshot_id)
            if snapshot is None:
                raise LookupError(f"Milestone Snapshot not found: {state.current_snapshot_id}")
            return snapshot, None
        except (sqlite3.Error, ValueError, LookupError) as error:
            return None, str(error)

    def _timeline(
        self,
        triage_id: str,
    ) -> tuple[tuple[ExecutionEvent, ...], str | None]:
        try:
            with self.database.read_only_connection() as connection:
                events = self.events.list_by_triage_id(connection, triage_id)
            return events[-self.history_limit :], None
        except (sqlite3.Error, ValueError) as error:
            return (), str(error)

    def _conversation(
        self,
        triage_id: str,
        activation: OwnerActivation | None,
    ) -> tuple[tuple[VisibleMessage, ...], str | None, bool]:
        try:
            with self.database.read_only_connection() as connection:
                owner = self.owners.get_by_triage_id(connection, triage_id)
                if owner is None:
                    return (), None, False
                histories = self.messages.list_by_session_id(
                    connection, owner.project_owner_session_id
                )
                activations = self.activations.list_by_triage_id(connection, triage_id)
            return (
                visible_messages(histories, activations),
                None,
                _activation_has_reply(histories, activation),
            )
        except (sqlite3.Error, ValueError) as error:
            return (), str(error), False


def _human_actions(
    state: ProjectRuntimeState,
    activation: OwnerActivation | None,
    active_stage: StageRun | None,
    runtime_error: str | None,
) -> tuple[FeatureAction, ...]:
    if runtime_error is not None or active_stage is not None:
        return ()
    if activation is not None:
        if (
            state.pending_action == "FIRST_RUN_APPROVAL"
            and activation.status is OwnerActivationStatus.PENDING
        ):
            return (FeatureAction.REJECT_FIRST_RUN,)
        if (
            state.pending_action == "BLOCKED_RUN_APPROVAL"
            and activation.status is OwnerActivationStatus.PENDING
        ):
            return (FeatureAction.REJECT_BLOCKED_RUN,)
        return ()
    if state.status == "TRIAGE":
        return (FeatureAction.BEGIN,)
    if state.pending_action == "PLAN_APPROVAL":
        return (FeatureAction.APPROVE_PLAN, FeatureAction.REJECT_PLAN)
    if state.pending_action == "FIRST_RUN_APPROVAL":
        return (FeatureAction.START_DELIVERY, FeatureAction.REJECT_FIRST_RUN)
    if state.pending_action == "BLOCKED_RUN_APPROVAL":
        return (
            FeatureAction.APPROVE_BLOCKED_RUN,
            FeatureAction.REJECT_BLOCKED_RUN,
        )
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
                    content=_read_optional_document(git.project_path / name),
                )
                for name in PLAN_DOCUMENT_NAMES
            ),
            None,
        )
    except OSError as error:
        return (), str(error)


def _read_optional_document(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def visible_messages(
    histories: tuple[MessageHistory, ...],
    activations: tuple[OwnerActivation, ...],
) -> tuple[VisibleMessage, ...]:
    from agentplanex.services.web.conversation_projection import ConversationProjection

    projection = ConversationProjection()
    projection.seed(histories, activations, max((item.sequence for item in histories), default=0))
    return projection.visible


def tool_calls(message: Message) -> tuple[tuple[str, str, object], ...]:
    candidates: list[object]
    if message.get("type") == "function_call":
        candidates = [message]
    elif message.get("object") == "response" and isinstance(message.get("output"), list):
        candidates = message["output"]
    else:
        return ()
    calls: list[tuple[str, str, object]] = []
    for item in candidates:
        if not isinstance(item, dict) or item.get("type") != "function_call":
            continue
        call_id = item.get("call_id")
        tool_name = item.get("name")
        if (
            isinstance(call_id, str)
            and call_id.strip()
            and isinstance(tool_name, str)
            and tool_name.strip()
        ):
            calls.append((call_id, tool_name, decoded_tool_arguments(item.get("arguments"))))
    return tuple(calls)


_TOOL_PREVIEW_LIMIT = 1_200
_SENSITIVE_KEY = re.compile(
    r"(?:api[_-]?key|authorization|cookie|credential|password|private[_-]?key|secret|token)",
    re.IGNORECASE,
)
_SENSITIVE_TEXT = re.compile(
    r"(api[_-]?key|authorization|cookie|credential|password|private[_-]?key|secret|token)"
    r"(\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)",
    re.IGNORECASE,
)
_BEARER_TOKEN = re.compile(r"\bBearer\s+[^\s,;]+", re.IGNORECASE)
_URL_CREDENTIALS = re.compile(
    r"(\b[a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@",
    re.IGNORECASE,
)
_KNOWN_SECRET = re.compile(
    r"\b(?:"
    r"sk-[A-Za-z0-9_-]{8,}"
    r"|gh[pousr]_[A-Za-z0-9_]{20,}"
    r"|glpat-[A-Za-z0-9_-]{10,}"
    r"|xox[baprs]-[A-Za-z0-9-]{10,}"
    r"|AIza[A-Za-z0-9_-]{20,}"
    r"|AKIA[A-Z0-9]{16}"
    r"|[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"
    r")\b"
)
_PRIVATE_KEY = re.compile(
    r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
    re.DOTALL,
)


def decoded_tool_arguments(arguments: object) -> object:
    if not isinstance(arguments, str):
        return arguments if arguments is not None else {}
    try:
        return json.loads(arguments)
    except json.JSONDecodeError:
        return arguments


def decoded_tool_output(output: object) -> object:
    if not isinstance(output, str):
        return output if output is not None else {}
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        return output


def tool_output_failed(output: object) -> bool:
    if not isinstance(output, dict):
        return False
    if output.get("ok") is False:
        return True
    returncode = output.get("returncode")
    if isinstance(returncode, int) and not isinstance(returncode, bool):
        return returncode != 0
    return bool(output.get("error_type")) or (
        bool(output.get("error")) and output.get("ok") is not True
    )


def tool_preview(value: object) -> str:
    sanitized = _sanitize_tool_value(value)
    if isinstance(sanitized, str):
        rendered = sanitized
    else:
        rendered = json.dumps(sanitized, ensure_ascii=False, indent=2, sort_keys=True)
    if len(rendered) <= _TOOL_PREVIEW_LIMIT:
        return rendered
    return f"{rendered[: _TOOL_PREVIEW_LIMIT - 1]}…"


def _sanitize_tool_value(value: object) -> object:
    if isinstance(value, dict):
        return {
            str(key): (
                "[redacted]" if _SENSITIVE_KEY.search(str(key)) else _sanitize_tool_value(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_tool_value(item) for item in value]
    if isinstance(value, str):
        redacted = _PRIVATE_KEY.sub("[redacted private key]", value)
        redacted = _URL_CREDENTIALS.sub(r"\1[redacted]@", redacted)
        redacted = _BEARER_TOKEN.sub("Bearer [redacted]", redacted)
        redacted = _SENSITIVE_TEXT.sub(r"\1\2[redacted]", redacted)
        return _KNOWN_SECRET.sub("[redacted]", redacted)
    return value


def _activation_has_reply(
    histories: tuple[MessageHistory, ...],
    activation: OwnerActivation | None,
) -> bool:
    if activation is None:
        return False
    inside_activation = False
    for history in histories:
        if history.message_id == activation.message_id:
            inside_activation = True
        if not inside_activation:
            continue
        for message in history.message:
            content = message.get("content")
            if (
                message.get("role") == "assistant" and isinstance(content, str) and content.strip()
            ) or assistant_response_text(message):
                return True
    return False


def assistant_response_text(message: Message) -> str:
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


def plan_decision_text(content: str) -> str:
    try:
        decision = json.loads(content)
    except json.JSONDecodeError:
        return "Plan decision recorded."
    if not isinstance(decision, dict):
        return "Plan decision recorded."
    label = str(decision.get("decision", "recorded")).lower()
    feedback = decision.get("feedback")
    return f"Plan {label}." + (f" Feedback: {feedback}" if feedback else "")
