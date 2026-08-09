"""Persistence and ReAct Loop execution for one logical Project Owner."""

import os
import sqlite3
from dataclasses import dataclass, field, replace
from pathlib import Path
from uuid import uuid4

from agentplanex.domains import (
    Action,
    AgentExit,
    AgentExitStatus,
    ExecutionEvent,
    ExecutionEventType,
    Message,
    MessageHistory,
    OwnerActivation,
    OwnerActivationStatus,
    ProjectOwnerAgent,
    ProjectOwnerTask,
    ProjectRuntimeContext,
    ToolExecutionResult,
    ToolExecutor,
)
from agentplanex.infrastructure.sqlite import SQLiteDatabase
from agentplanex.infrastructure.sqlite.repositories import (
    SQLiteMessageHistoryRepository,
    SQLiteProjectOwnerAgentRepository,
    SQLiteProjectRuntimeContextRepository,
)
from agentplanex.project_owner_agent.agent import AgentConfig, DefaultAgent
from agentplanex.project_owner_agent.approval import ApprovalMode, TerminalApproval
from agentplanex.project_owner_agent.exception import AgentFlowExit
from agentplanex.project_owner_agent.interactive import InteractiveAgent
from agentplanex.project_owner_agent.models.jbb import (
    JBBModel,
    JBBResponses,
    format_tool_call_message,
    format_tool_output_message,
)
from agentplanex.project_owner_agent.tools import ToolCatalog
from agentplanex.services.agent_contracts import (
    AgentPromptCatalog,
    InvocationContract,
    PromptRole,
)
from agentplanex.services.event_bus import EventBus
from agentplanex.services.owner_context import ProjectOwnerContextQuery
from agentplanex.services.owner_context_memory import ProjectOwnerContextMemory
from agentplanex.settings import Settings


@dataclass(slots=True)
class ProjectOwnerService:
    """Own persistent Owner identity, native messages, and one ReAct Loop."""

    database: SQLiteDatabase
    settings: Settings
    approval_mode: ApprovalMode
    tools: ToolCatalog
    tool_executor: ToolExecutor
    event_bus: EventBus
    owner_contexts: ProjectOwnerContextQuery
    context_memory: ProjectOwnerContextMemory
    responses: JBBResponses
    observation_skill: Path
    prompts: AgentPromptCatalog
    contexts: SQLiteProjectRuntimeContextRepository = field(
        default_factory=SQLiteProjectRuntimeContextRepository
    )
    owners: SQLiteProjectOwnerAgentRepository = field(
        default_factory=SQLiteProjectOwnerAgentRepository
    )
    messages: SQLiteMessageHistoryRepository = field(
        default_factory=SQLiteMessageHistoryRepository
    )

    def __post_init__(self) -> None:
        if self.approval_mode not in {"confirm", "yolo"}:
            raise ValueError(f"Unknown approval mode: {self.approval_mode!r}")

    def ensure_state(
        self,
        connection: sqlite3.Connection,
    ) -> ProjectRuntimeContext:
        """Return the sole project Context and create its Owner when absent."""
        tool_names = tuple(tool.name for tool in self.tools.tools)
        existing_contexts = self.contexts.list_all(connection)
        if len(existing_contexts) > 1:
            raise ValueError("Project contains more than one Project Runtime context")
        if existing_contexts:
            context = existing_contexts[0]
        else:
            context = ProjectRuntimeContext(triage_id=uuid4().hex)
            self.contexts.insert(connection, context)

        owner = self.owners.get_by_triage_id(connection, context.triage_id)
        configured_prompt = self.prompts.role_instructions(PromptRole.PROJECT_OWNER)
        if owner is None:
            owner = ProjectOwnerAgent(
                triage_id=context.triage_id,
                project_owner_session_id=uuid4().hex,
                system_prompt=configured_prompt,
                tools=tool_names,
            )
            self.owners.insert(connection, owner)
        else:
            self.tools.select(owner.tools)

        return replace(context, project_owner_agent=owner)

    def append_task(
        self,
        connection: sqlite3.Connection,
        context: ProjectRuntimeContext,
        task: ProjectOwnerTask,
    ) -> tuple[str, str | None]:
        """Persist external input and return its message and frozen Summary IDs."""
        content = task.content.strip()
        if not content:
            raise ValueError("Project Owner task content must not be empty")
        owner = context.project_owner_agent
        if owner is None:
            raise ValueError("Project Runtime Context has no Project Owner Agent")

        appended: list[Message] = []
        if owner.message_id is None:
            appended.append({"role": "system", "content": owner.system_prompt})
        appended.append({"role": "user", "content": content})
        message_id = self._append_messages(connection, owner, tuple(appended))
        return message_id, owner.summary_id

    def run_activation(self, activation: OwnerActivation) -> AgentExit:
        """Restore persisted Owner history and run exactly one activation."""
        try:
            context, messages = self._load_state_for_activation(activation)
            owner = context.project_owner_agent
            if owner is None:
                raise RuntimeError("Project Owner was not restored")
            agent = self._build_agent(messages, owner.system_prompt, owner, activation)
        except Exception as error:
            return _unhandled_exit(error)

        react_loop_id = activation.activation_id
        self.event_bus.publish(
            ExecutionEvent(
                triage_id=context.triage_id,
                event_type=ExecutionEventType.REACT_LOOP_ENTERED,
                react_loop_id=react_loop_id,
                payload={
                    "task_type": activation.task_type.value,
                    "driver_mode": "MODEL",
                },
            )
        )
        try:
            agent.run(context)
        except AgentFlowExit as error:
            result = AgentExit(status=error.status, content=error.content)
        except Exception as error:
            result = _unhandled_exit(error)
        self.event_bus.publish(
            ExecutionEvent(
                triage_id=context.triage_id,
                event_type=ExecutionEventType.REACT_LOOP_EXITED,
                react_loop_id=react_loop_id,
                payload={
                    "agent_exit_status": result.status.value,
                    "driver_mode": "MODEL",
                },
            )
        )
        return result

    def execute_action(self, action: Action) -> ToolExecutionResult:
        """Execute one explicit debug Tool Action against current persisted state."""
        with self.database.transaction() as connection:
            context = self.ensure_state(connection)
        return self.tool_executor(context, action)

    def execute_activation_action(
        self,
        activation: OwnerActivation,
        action: Action,
    ) -> ToolExecutionResult:
        """Execute and persist one Tool step inside a claimed manual Owner loop."""

        context, _ = self._load_state_for_activation(
            activation,
            allow_advanced_checkpoint=True,
        )
        self.append_messages(context, (format_tool_call_message(action),))
        try:
            result = self._execute_latest_context(context, action)
        except Exception as error:
            self.append_messages(
                context,
                (
                    format_tool_output_message(
                        action,
                        {
                            "ok": False,
                            "error": f"{type(error).__name__}: {error}",
                        }
                    ),
                ),
            )
            raise
        self.append_messages(
            context,
            (format_tool_output_message(action, result.output),),
        )
        return result

    def reply_to_activation(
        self,
        activation: OwnerActivation,
        content: str,
    ) -> AgentExit:
        """Persist a manual Owner reply and end the claimed loop."""

        reply = content.strip()
        if not reply:
            raise ValueError("Project Owner reply must not be empty")
        context, _ = self._load_state_for_activation(
            activation,
            allow_advanced_checkpoint=True,
        )
        self.append_messages(context, ({"role": "assistant", "content": reply},))
        return AgentExit(status=AgentExitStatus.REPLY_TO_HUMAN, content=reply)

    def _load_state_for_activation(
        self,
        activation: OwnerActivation,
        *,
        allow_advanced_checkpoint: bool = False,
    ) -> tuple[ProjectRuntimeContext, tuple[Message, ...]]:
        if activation.status is not OwnerActivationStatus.RUNNING:
            raise ValueError(
                "Project Owner can only run a claimed activation: "
                f"{activation.activation_id} is {activation.status.value}"
            )
        with self.database.connection() as connection:
            context = self.ensure_state(connection)
            if context.triage_id != activation.triage_id:
                raise LookupError(
                    "Owner activation does not belong to this Project Runtime"
                )
            owner = context.project_owner_agent
            if owner is None:
                raise RuntimeError("Project Owner was not created")
            if (
                owner.message_id != activation.message_id
                and not allow_advanced_checkpoint
            ):
                raise RuntimeError(
                    "Owner activation checkpoint is not the current message: "
                    f"{activation.message_id} != {owner.message_id}"
                )
            messages = self._load_messages(connection, owner, activation)
            messages = self._with_activation_contract(messages, context, activation)
        return context, messages

    def _with_activation_contract(
        self,
        messages: tuple[Message, ...],
        context: ProjectRuntimeContext,
        activation: OwnerActivation,
    ) -> tuple[Message, ...]:
        if not messages or messages[0].get("role") != "system":
            raise RuntimeError("Restored Owner context has no System Prompt")
        fixed_work_object = {
            "activation_id": activation.activation_id,
            "message_id": activation.message_id,
            "runtime_status": context.status,
            "pending_action": context.pending_action,
            "current_plan_commit_sha": context.current_plan_commit_sha,
            "current_snapshot_id": context.current_snapshot_id,
            "current_run_id": context.current_run_id,
            "current_milestone_key": context.current_milestone_key,
            "current_stage_key": context.current_stage_key,
            "current_candidate_commit_sha": context.current_candidate_commit_sha,
        }
        envelope = self.prompts.render_invocation(
            InvocationContract(
                role=PromptRole.PROJECT_OWNER,
                operation=f"owner_activation:{activation.task_type.value}",
                project_root=self.database.path.parent.parent,
                observation_skill=self.observation_skill,
                triage_id=context.triage_id,
                fixed_work_object=fixed_work_object,
                workspace={
                    "project_repository": "read_only",
                    "runtime_mutation": "exposed_tools_only",
                },
                output_contract={
                    "one_of": ["tool_action", "concise_user_reply"],
                },
            )
        )
        system = dict(messages[0])
        system["content"] = f"{system.get('content', '')}\n\n{envelope}"
        return (system, *messages[1:])

    def _build_agent(
        self,
        messages: tuple[Message, ...],
        system_prompt: str,
        owner: ProjectOwnerAgent,
        activation: OwnerActivation,
    ) -> DefaultAgent:
        owner_settings = self.settings.project_owner_agent
        model_settings = owner_settings.model
        fixed_tools = self.tools.select(owner.tools)
        model = JBBModel(
            model=model_settings.name,
            tools=fixed_tools,
            base_url=model_settings.base_url,
            timeout_seconds=model_settings.timeout_seconds,
            transport=self.responses.transport,
        )
        config = AgentConfig(
            system_prompt=system_prompt,
            step_limit=owner_settings.step_limit,
            max_consecutive_format_errors=owner_settings.max_consecutive_format_errors,
        )
        return (
            DefaultAgent(
                model,
                self._execute_latest_context,
                append_messages=self.append_messages,
                initial_messages=messages,
                prepare_query=lambda context, query_index, current: (
                    self.context_memory.prepare_query(
                        context,
                        activation,
                        query_index,
                        current,
                    )
                ),
                config=config,
            )
            if self.approval_mode == "yolo"
            else InteractiveAgent(
                model,
                self._execute_latest_context,
                append_messages=self.append_messages,
                initial_messages=list(messages),
                prepare_query=lambda context, query_index, current: (
                    self.context_memory.prepare_query(
                        context,
                        activation,
                        query_index,
                        current,
                    )
                ),
                approval=TerminalApproval(
                    require_tty=os.getenv(
                        "AGENTPLANEX_REQUIRE_INTERACTIVE_TERMINAL", "1"
                    )
                    != "0"
                ),
                config=config,
            )
        )

    def _execute_latest_context(
        self,
        context: ProjectRuntimeContext,
        action: Action,
    ) -> ToolExecutionResult:
        with self.database.connection() as connection:
            current = self.contexts.get(connection, context.triage_id)
        if current is None:
            raise LookupError(f"Project Runtime Context not found: {context.triage_id}")
        return self.tool_executor(
            replace(current, project_owner_agent=context.project_owner_agent),
            action,
        )

    def append_messages(
        self,
        context: ProjectRuntimeContext,
        appended: tuple[Message, ...],
    ) -> None:
        """Atomically append native Owner messages and advance its checkpoint."""
        if not appended:
            return
        owner = context.project_owner_agent
        if owner is None:
            raise ValueError("Project Runtime Context has no Project Owner Agent")

        with self.database.transaction() as connection:
            persisted_owner = self.owners.get_by_session_id(
                connection,
                owner.project_owner_session_id,
            )
            if persisted_owner is None:
                raise LookupError(
                    "Project Owner Agent not found: "
                    f"{owner.project_owner_session_id}"
                )
            self._append_messages(connection, persisted_owner, appended)

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

    def _load_messages(
        self,
        connection: sqlite3.Connection,
        owner: ProjectOwnerAgent,
        activation: OwnerActivation,
    ) -> tuple[Message, ...]:
        latest = self.messages.get_latest_by_session_id(
            connection,
            owner.project_owner_session_id,
        )
        latest_id = latest.message_id if latest is not None else None
        if latest_id != owner.message_id:
            raise RuntimeError(
                "Project Owner Agent latest message pointer does not match message history"
            )

        restored = self.owner_contexts.restore_in_connection(
            connection,
            activation.message_id,
            summary_id=activation.summary_id,
        )
        if (
            restored.triage_id != owner.triage_id
            or restored.project_owner_session_id != owner.project_owner_session_id
        ):
            raise RuntimeError(
                "Restored Owner context does not match the Activation owner"
            )
        return restored.messages


def _unhandled_exit(error: Exception) -> AgentExit:
    return AgentExit(
        status=AgentExitStatus.UNHANDLED_EXCEPTION,
        content=f"{type(error).__name__}: {error}",
    )
