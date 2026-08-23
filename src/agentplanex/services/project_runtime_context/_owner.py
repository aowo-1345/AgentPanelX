"""Persistence and ReAct Loop execution for one logical Project Owner."""

import os
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from hashlib import sha256
from uuid import uuid4

from agentplanex.domains.execution_event import (
    ExecutionEvent,
    ExecutionEventType,
)
from agentplanex.domains.project_owner_agent import ProjectOwnerAgent
from agentplanex.domains.project_runtime_state import ProjectRuntimeState
from agentplanex.infrastructure.sqlite import SQLiteDatabase
from agentplanex.infrastructure.sqlite.repositories import (
    SQLiteMessageHistoryRepository,
    SQLiteProjectOwnerAgentRepository,
    SQLiteSummaryHistoryRepository,
)
from agentplanex.project_owner_agent.agent import AgentConfig, DefaultAgent
from agentplanex.project_owner_agent.approval import ApprovalMode, TerminalApproval
from agentplanex.project_owner_agent.context.compaction import (
    ContextCompactionNotice,
    ContextCompactionPhase,
    OwnerContextPolicy,
    SummaryDraft,
)
from agentplanex.project_owner_agent.context.manager import (
    CommittedOwnerSummary,
    LoadedOwnerContext,
    OwnerContextManager,
    OwnerContextSnapshot,
)
from agentplanex.project_owner_agent.context.models import MessageHistory, SummaryHistory
from agentplanex.project_owner_agent.contracts import (
    Action,
    AgentExit,
    AgentExitStatus,
    Message,
    ToolExecutionResult,
)
from agentplanex.project_owner_agent.exception import AgentFlowExit
from agentplanex.project_owner_agent.interactive import InteractiveAgent
from agentplanex.project_owner_agent.models.responses import (
    ProjectOwnerModel,
    ResponsesClient,
    format_tool_call_message,
    format_tool_output_message,
)
from agentplanex.project_owner_agent.tools import ToolCatalog
from agentplanex.services.agent_invocation import (
    AgentPromptCatalog,
    InvocationRole,
)
from agentplanex.services.event_bus import EventBus
from agentplanex.services.owner_history import select_owner_context_snapshot
from agentplanex.services.project_runtime_context.contracts import RuntimeToolExecutor
from agentplanex.services.project_runtime_context.models import (
    OwnerActivation,
    OwnerActivationStatus,
    ProjectOwnerTask,
)
from agentplanex.settings import Settings


@dataclass(frozen=True, slots=True)
class _ProjectOwnerRevision:
    """Runtime-private CAS state carried opaquely by the Owner Agent."""

    message_id: str
    summary_id: str | None


@dataclass(slots=True)
class _OwnerRuntime:
    """Private Owner identity, history, Agent, and Tool execution environment."""

    database: SQLiteDatabase
    settings: Settings
    approval_mode: ApprovalMode
    tools: ToolCatalog
    tool_executor: RuntimeToolExecutor
    event_bus: EventBus
    responses: ResponsesClient
    prompts: AgentPromptCatalog
    load_state: Callable[[], ProjectRuntimeState]
    set_activation_initial_summary: Callable[
        [sqlite3.Connection, str, str],
        None,
    ]
    owners: SQLiteProjectOwnerAgentRepository = field(
        default_factory=SQLiteProjectOwnerAgentRepository
    )
    messages: SQLiteMessageHistoryRepository = field(default_factory=SQLiteMessageHistoryRepository)
    summaries: SQLiteSummaryHistoryRepository = field(
        default_factory=SQLiteSummaryHistoryRepository
    )

    def __post_init__(self) -> None:
        if self.approval_mode not in {"confirm", "yolo"}:
            raise ValueError(f"Unknown approval mode: {self.approval_mode!r}")

    def restore_identity(
        self,
        connection: sqlite3.Connection,
        state: ProjectRuntimeState,
    ) -> ProjectOwnerAgent:
        """Restore and validate the Owner identity for an initialized State."""
        owner = self.owners.get_by_triage_id(connection, state.triage_id)
        if owner is None:
            raise RuntimeError("Initialized Project Runtime has no Owner identity")
        self.tools.select(owner.tools)
        return owner

    def create_identity(
        self,
        connection: sqlite3.Connection,
        state: ProjectRuntimeState,
    ) -> ProjectOwnerAgent:
        """Create the sole persistent Owner identity with the configured contract."""
        owner = ProjectOwnerAgent(
            triage_id=state.triage_id,
            project_owner_session_id=uuid4().hex,
            system_prompt=self.prompts.role_instructions(InvocationRole.PROJECT_OWNER),
            tools=tuple(tool.name for tool in self.tools.tools),
        )
        self.owners.insert(connection, owner)
        return owner

    def append_task(
        self,
        connection: sqlite3.Connection,
        state: ProjectRuntimeState,
        task: ProjectOwnerTask,
    ) -> tuple[str, str | None]:
        """Persist external input and return its message and frozen Summary IDs."""
        content = task.content.strip()
        if not content:
            raise ValueError("Project Owner task content must not be empty")
        owner = self.owners.get_by_triage_id(connection, state.triage_id)
        if owner is None:
            raise RuntimeError("Initialized Project Runtime has no Owner identity")

        appended: list[Message] = []
        if owner.message_id is None:
            appended.append({"role": "system", "content": owner.system_prompt})
        appended.append({"role": "user", "content": content})
        message_id = self._append_messages(connection, owner, tuple(appended))
        return message_id, owner.summary_id

    def current_message_id(
        self,
        connection: sqlite3.Connection,
        state: ProjectRuntimeState,
    ) -> str | None:
        """Read the current Owner checkpoint without caching its revision."""
        return self.restore_identity(connection, state).message_id

    def run_activation(
        self,
        state: ProjectRuntimeState,
        activation: OwnerActivation,
    ) -> AgentExit:
        """Restore persisted Owner history and run exactly one activation."""
        try:
            owner = self._load_owner_for_activation(state, activation)
            agent = self._build_agent(state, owner, activation)
        except Exception as error:
            return _unhandled_exit(error)

        try:
            agent.run()
        except AgentFlowExit as error:
            result = AgentExit(status=error.status, content=error.content)
        except Exception as error:
            result = _unhandled_exit(error)
        else:
            result = _unhandled_exit(RuntimeError("Project Owner Agent returned without an exit"))
        return result

    def execute_activation_action(
        self,
        state: ProjectRuntimeState,
        activation: OwnerActivation,
        action: Action,
    ) -> ToolExecutionResult:
        """Execute and persist one Tool step inside a claimed manual Owner loop."""

        self._load_owner_for_activation(
            state,
            activation,
            allow_advanced_checkpoint=True,
        )
        self.append_messages(state, (format_tool_call_message(action),))
        try:
            result = self._execute_latest_context(action)
        except Exception as error:
            self.append_messages(
                state,
                (
                    format_tool_output_message(
                        action,
                        {
                            "ok": False,
                            "error": f"{type(error).__name__}: {error}",
                        },
                    ),
                ),
            )
            raise
        self.append_messages(
            state,
            (format_tool_output_message(action, result.output),),
        )
        return result

    def append_reply(
        self,
        connection: sqlite3.Connection,
        state: ProjectRuntimeState,
        activation: OwnerActivation,
        content: str,
    ) -> AgentExit:
        """Persist a manual reply inside the caller's terminalization transaction."""

        reply = content.strip()
        if not reply:
            raise ValueError("Project Owner reply must not be empty")
        owner = self._load_owner_for_activation_in_connection(
            connection,
            state,
            activation,
            allow_advanced_checkpoint=True,
        )
        self._append_messages(
            connection,
            owner,
            ({"role": "assistant", "content": reply},),
        )
        return AgentExit(status=AgentExitStatus.REPLY_TO_HUMAN, content=reply)

    def _load_owner_for_activation(
        self,
        state: ProjectRuntimeState,
        activation: OwnerActivation,
        *,
        allow_advanced_checkpoint: bool = False,
    ) -> ProjectOwnerAgent:
        if activation.status is not OwnerActivationStatus.RUNNING:
            raise ValueError(
                "Project Owner can only run a claimed activation: "
                f"{activation.activation_id} is {activation.status.value}"
            )
        with self.database.connection() as connection:
            return self._load_owner_for_activation_in_connection(
                connection,
                state,
                activation,
                allow_advanced_checkpoint=allow_advanced_checkpoint,
            )

    def _load_owner_for_activation_in_connection(
        self,
        connection: sqlite3.Connection,
        state: ProjectRuntimeState,
        activation: OwnerActivation,
        *,
        allow_advanced_checkpoint: bool = False,
    ) -> ProjectOwnerAgent:
        if activation.status is not OwnerActivationStatus.RUNNING:
            raise ValueError(
                "Project Owner can only run a claimed activation: "
                f"{activation.activation_id} is {activation.status.value}"
            )
        if state.triage_id != activation.triage_id:
            raise LookupError("Owner activation does not belong to this Project Runtime")
        owner = self.restore_identity(connection, state)
        if owner.message_id != activation.message_id and not allow_advanced_checkpoint:
            raise RuntimeError(
                "Owner activation checkpoint is not the current message: "
                f"{activation.message_id} != {owner.message_id}"
            )
        return owner

    def _build_agent(
        self,
        context: ProjectRuntimeState,
        owner: ProjectOwnerAgent,
        activation: OwnerActivation,
    ) -> DefaultAgent:
        owner_settings = self.settings.project_owner_agent
        fixed_tools = self.tools.select(owner.tools)
        owner_responses = self.responses.with_cache_affinity(
            _cache_affinity(owner.project_owner_session_id, purpose="owner")
        )
        summary_responses = self.responses.with_cache_affinity(
            _cache_affinity(owner.project_owner_session_id, purpose="summary")
        )
        model = ProjectOwnerModel(
            tools=fixed_tools,
            responses=owner_responses,
        )
        config = AgentConfig(
            step_limit=owner_settings.step_limit,
            max_consecutive_format_errors=owner_settings.max_consecutive_format_errors,
        )
        owner_context = OwnerContextManager.restore(
            runtime=self,
            runtime_context=context,
            activation=activation,
            policy=OwnerContextPolicy(
                model_name=owner_settings.selected_model.name,
                capacity_tokens=owner_settings.context_memory.capacity_tokens,
                compaction_threshold=(owner_settings.context_memory.compaction_threshold),
                summary_context_header=(self.settings.runtime.prompts.summary_context_header),
                trajectory_summary_prompt=(self.settings.runtime.prompts.trajectory_summary),
                initial_intent_summary_prompt=(
                    self.settings.runtime.prompts.initial_intent_summary
                ),
                update_intent_summary_prompt=(self.settings.runtime.prompts.update_intent_summary),
            ),
            tools=fixed_tools,
            summary_model=summary_responses,
        )

        return (
            DefaultAgent(
                model,
                self._execute_latest_context,
                owner_context=owner_context,
                config=config,
            )
            if self.approval_mode == "yolo"
            else InteractiveAgent(
                model,
                self._execute_latest_context,
                owner_context=owner_context,
                approval=TerminalApproval(
                    require_tty=os.getenv("AGENTPLANEX_REQUIRE_INTERACTIVE_TERMINAL", "1") != "0"
                ),
                config=config,
            )
        )

    def _execute_latest_context(
        self,
        action: Action,
    ) -> ToolExecutionResult:
        return self.tool_executor(self.load_state(), action)

    def append_messages(
        self,
        context: ProjectRuntimeState,
        appended: tuple[Message, ...],
        *,
        expected_revision: object | None = None,
    ) -> object:
        """Atomically append native Owner messages and advance its checkpoint."""
        expected = (
            _require_owner_revision(expected_revision) if expected_revision is not None else None
        )

        with self.database.transaction() as connection:
            persisted_owner = self.owners.get_by_triage_id(connection, context.triage_id)
            if persisted_owner is None:
                raise RuntimeError("Initialized Project Runtime has no Owner identity")
            if expected is not None and persisted_owner.message_id != expected.message_id:
                raise RuntimeError("Project Owner context changed before message append")
            if appended:
                message_id = self._append_messages(
                    connection,
                    persisted_owner,
                    appended,
                )
            elif persisted_owner.message_id is not None:
                message_id = persisted_owner.message_id
            else:
                raise RuntimeError("Project Owner has no persisted message checkpoint")
        return _ProjectOwnerRevision(
            message_id=message_id,
            summary_id=(
                expected.summary_id if expected is not None else persisted_owner.summary_id
            ),
        )

    def commit_summary(
        self,
        context: ProjectRuntimeState,
        activation: OwnerActivation,
        *,
        expected_revision: object,
        query_index: int,
        draft: SummaryDraft,
    ) -> CommittedOwnerSummary:
        """Atomically persist one Agent-produced Summary at an expected revision."""

        expected = _require_owner_revision(expected_revision)
        with self.database.transaction() as connection:
            owner = self.owners.get_by_triage_id(connection, context.triage_id)
            if owner is None:
                raise RuntimeError("Initialized Project Runtime has no Owner identity")
            summary = SummaryHistory(
                project_owner_session_id=owner.project_owner_session_id,
                summary_id=uuid4().hex,
                covered_through_message_id=expected.message_id,
                intent_summary_content=draft.intent_summary_content,
                trajectory_summary_content=draft.trajectory_summary_content,
            )
            self.summaries.insert(connection, summary)
            self.owners.advance_summary(
                connection,
                session_id=owner.project_owner_session_id,
                expected_message_id=expected.message_id,
                expected_summary_id=expected.summary_id,
                summary_id=summary.summary_id,
            )
            if query_index == 0:
                self.set_activation_initial_summary(
                    connection,
                    activation.activation_id,
                    summary.summary_id,
                )
        return CommittedOwnerSummary(
            summary=summary,
            revision=_ProjectOwnerRevision(
                message_id=expected.message_id,
                summary_id=summary.summary_id,
            ),
        )

    def record_compaction(
        self,
        context: ProjectRuntimeState,
        activation: OwnerActivation,
        notice: ContextCompactionNotice,
        *,
        revision: object,
    ) -> None:
        """Map an Agent context notice to the stable Runtime Timeline contract."""

        attempt = notice.attempt
        attempted_revision = _require_owner_revision(revision)
        event_type = {
            ContextCompactionPhase.STARTED: (ExecutionEventType.CONTEXT_COMPACTION_STARTED),
            ContextCompactionPhase.COMPLETED: (ExecutionEventType.CONTEXT_COMPACTION_COMPLETED),
            ContextCompactionPhase.FAILED: (ExecutionEventType.CONTEXT_COMPACTION_FAILED),
        }[notice.phase]
        payload: dict[str, object] = {
            "compaction_id": attempt.compaction_id,
            "activation_id": activation.activation_id,
            "query_index": attempt.query_index,
            "covered_through_message_id": attempted_revision.message_id,
            "estimated_tokens": attempt.estimated_tokens,
            "capacity_tokens": attempt.capacity_tokens,
            "compaction_threshold": attempt.compaction_threshold,
        }
        if notice.summary_id is not None:
            payload["summary_id"] = notice.summary_id
        if notice.failure_type is not None:
            payload["failure_type"] = notice.failure_type
        self.event_bus.publish(
            ExecutionEvent(
                triage_id=context.triage_id,
                event_type=event_type,
                react_loop_id=activation.activation_id,
                payload=payload,
            )
        )

    def _append_messages(
        self,
        connection: sqlite3.Connection,
        owner: ProjectOwnerAgent,
        appended: tuple[Message, ...],
    ) -> str:
        history = MessageHistory(
            project_owner_session_id=owner.project_owner_session_id,
            message_id=uuid4().hex,
            sequence=self.messages.next_sequence(
                connection,
                owner.project_owner_session_id,
            ),
            message=tuple(dict(message) for message in appended),
        )
        self.messages.insert(connection, history)
        self.owners.update(
            connection,
            replace(owner, message_id=history.message_id),
        )
        return history.message_id

    def load_context(
        self,
        context: ProjectRuntimeState,
        activation: OwnerActivation,
    ) -> LoadedOwnerContext:
        """Load raw persisted facts for one fixed Activation checkpoint."""

        restored, current_revision = _load_live_owner_context(
            self.database,
            activation.message_id,
            summary_id=activation.summary_id,
        )
        if restored.triage_id != context.triage_id:
            raise RuntimeError("Restored Owner context does not match the Activation owner")
        if current_revision.message_id != activation.message_id:
            raise RuntimeError("Owner activation checkpoint changed while restoring context")
        return LoadedOwnerContext(
            snapshot=restored,
            revision=_ProjectOwnerRevision(
                message_id=activation.message_id,
                summary_id=activation.summary_id,
            ),
        )


def _load_live_owner_context(
    database: SQLiteDatabase,
    through_message_id: str,
    *,
    summary_id: str | None = None,
) -> tuple[OwnerContextSnapshot, _ProjectOwnerRevision]:
    """Select a checkpoint and validate the live Owner pointer in one read."""

    owners = SQLiteProjectOwnerAgentRepository()
    messages = SQLiteMessageHistoryRepository()
    with database.read_only_connection() as connection:
        snapshot = select_owner_context_snapshot(
            connection,
            through_message_id,
            summary_id=summary_id,
        )
        owner = owners.get_by_session_id(
            connection,
            snapshot.project_owner_session_id,
        )
        if owner is None:
            raise LookupError(
                "Project Owner Agent not found for message checkpoint: "
                f"{snapshot.through_message_id}"
            )
        if owner.message_id is None:
            raise RuntimeError("Project Owner has no persisted message checkpoint")
        latest = messages.get_latest_by_session_id(
            connection,
            owner.project_owner_session_id,
        )
        latest_id = latest.message_id if latest is not None else None
        if latest_id != owner.message_id:
            raise RuntimeError(
                "Project Owner Agent latest message pointer does not match message history"
            )
    return snapshot, _ProjectOwnerRevision(
        message_id=owner.message_id,
        summary_id=owner.summary_id,
    )


def _unhandled_exit(error: Exception) -> AgentExit:
    return AgentExit(
        status=AgentExitStatus.UNHANDLED_EXCEPTION,
        content=f"{type(error).__name__}: {error}",
    )


def _cache_affinity(session_id: str, *, purpose: str) -> str:
    """Derive an opaque, stable request affinity without persisting another fact."""

    material = f"agentplanex:model-cache:v1:{purpose}:{session_id}"
    return sha256(material.encode("utf-8")).hexdigest()


def _require_owner_revision(revision: object) -> _ProjectOwnerRevision:
    if not isinstance(revision, _ProjectOwnerRevision):
        raise TypeError("Owner context revision was not issued by this Runtime")
    return revision
