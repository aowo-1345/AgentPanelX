"""Web-ready projection with independently degradable Feature panels."""

import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from agentplanex.domains import (
    ExecutionEvent,
    FeatureAction,
    Message,
    MessageHistory,
    MilestoneSnapshot,
    OwnerActivation,
    OwnerActivationStatus,
    ProjectOwnerTaskType,
    ProjectRuntimeState,
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
    SQLiteProjectRuntimeStateRepository,
    SQLiteStageRunRepository,
)
from agentplanex.services.planning import SPEC_DOCUMENT_NAMES

type ToolActivityStatus = Literal["running", "completed", "failed"]


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
class PlanDocument:
    name: str
    content: str | None


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
    history_limit: int = 50

    def get(self, triage_id: str) -> ProjectWorkspaceView:
        state = self._state(triage_id)
        activation, active_stage, runtime_error = self._runtime(triage_id)
        snapshot, milestones_error = self._milestones(state)
        timeline, timeline_error = self._timeline(triage_id)
        conversation, conversation_error, activation_has_reply = self._conversation(
            triage_id,
            activation,
        )
        plan_documents, plan_error = _read_plan_documents(self.git)
        branch, head, git_error = _git_panel(self.git)
        return ProjectWorkspaceView(
            state=state,
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
                _visible_messages(histories, activations),
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
    if runtime_error is not None or activation is not None or active_stage is not None:
        return ()
    if state.status == "TRIAGE":
        return (FeatureAction.BEGIN,)
    if state.pending_action == "PLAN_APPROVAL":
        return (FeatureAction.APPROVE_PLAN, FeatureAction.REJECT_PLAN)
    if state.pending_action == "FIRST_RUN_APPROVAL":
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
                    content=_read_optional_document(git.project_path / name),
                )
                for name in SPEC_DOCUMENT_NAMES
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


def _visible_messages(
    histories: tuple[MessageHistory, ...],
    activations: tuple[OwnerActivation, ...],
) -> tuple[VisibleMessage, ...]:
    activation_by_message = {item.message_id: item for item in activations}
    tool_index_by_call_id: dict[str, int] = {}
    visible: list[VisibleMessage] = []
    current_activation: OwnerActivation | None = None
    for history in histories:
        activation = activation_by_message.get(history.message_id)
        if activation is not None:
            current_activation = activation
        for index, message in enumerate(history.message):
            tool_calls = _tool_calls(message)
            for call_id, tool_name, arguments in tool_calls:
                tool_index_by_call_id[call_id] = len(visible)
                visible.append(
                    VisibleMessage(
                        f"{history.message_id}:{index}:tool:{call_id}",
                        "tool",
                        tool_name,
                        ToolActivity(
                            name=tool_name,
                            status=(
                                "running"
                                if current_activation is not None
                                and current_activation.status is OwnerActivationStatus.RUNNING
                                else "failed"
                            ),
                            input_preview=_tool_preview(arguments),
                        ),
                    )
                )
            response_text = _assistant_response_text(message)
            if response_text:
                visible.append(
                    VisibleMessage(f"{history.message_id}:{index}", "assistant", response_text)
                )
                continue
            if tool_calls:
                continue
            if message.get("type") == "function_call_output":
                output_call_id = message.get("call_id")
                output = _decoded_tool_output(message.get("output"))
                output_preview = _tool_preview(output)
                status: ToolActivityStatus = (
                    "failed" if _tool_output_failed(output) else "completed"
                )
                tool_index = (
                    tool_index_by_call_id.get(output_call_id)
                    if isinstance(output_call_id, str)
                    else None
                )
                if tool_index is not None:
                    current = visible[tool_index]
                    activity = current.tool_activity
                    if activity is not None:
                        visible[tool_index] = VisibleMessage(
                            message_id=current.message_id,
                            role="tool",
                            content=current.content,
                            tool_activity=ToolActivity(
                                name=activity.name,
                                status=status,
                                input_preview=activity.input_preview,
                                output_preview=output_preview,
                            ),
                        )
                else:
                    visible.append(
                        VisibleMessage(
                            f"{history.message_id}:{index}:tool:{output_call_id}",
                            "tool",
                            "tool",
                            ToolActivity(
                                name="tool",
                                status=status,
                                input_preview="{}",
                                output_preview=output_preview,
                            ),
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
                    visible.append(VisibleMessage(f"{history.message_id}:{index}", "user", content))
                elif activation.task_type is ProjectOwnerTaskType.PLAN_DECISION:
                    visible.append(
                        VisibleMessage(
                            f"{history.message_id}:{index}",
                            "status",
                            _plan_decision_text(content),
                        )
                    )
        if activation is not None and activation.failure is not None:
            visible.append(
                VisibleMessage(
                    f"{activation.activation_id}:failure",
                    "status",
                    f"Project Owner failed: {activation.failure}",
                )
            )
    return tuple(visible)


def _tool_calls(message: Message) -> tuple[tuple[str, str, object], ...]:
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
            calls.append((call_id, tool_name, _decoded_tool_arguments(item.get("arguments"))))
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


def _decoded_tool_arguments(arguments: object) -> object:
    if not isinstance(arguments, str):
        return arguments if arguments is not None else {}
    try:
        return json.loads(arguments)
    except json.JSONDecodeError:
        return arguments


def _decoded_tool_output(output: object) -> object:
    if not isinstance(output, str):
        return output if output is not None else {}
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        return output


def _tool_output_failed(output: object) -> bool:
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


def _tool_preview(value: object) -> str:
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
            ) or _assistant_response_text(message):
                return True
    return False


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
