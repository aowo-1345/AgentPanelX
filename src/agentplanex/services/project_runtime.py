"""Long-lived Project Owner session and persistence service."""

import os
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
    ProjectOwnerAgent,
    ProjectOwnerTask,
    ProjectOwnerTaskType,
    ProjectRuntimeContext,
    RuntimeContextChangeReason,
    ToolExecutionResult,
    ToolExecutor,
    UserInteractionAction,
)
from agentplanex.infrastructure.sqlite import SQLiteDatabase, initialize_schema
from agentplanex.infrastructure.sqlite.repositories import (
    SQLiteMessageHistoryRepository,
    SQLiteProjectOwnerAgentRepository,
    SQLiteProjectRuntimeContextRepository,
)
from agentplanex.project_owner_agent.agent import AgentConfig, DefaultAgent
from agentplanex.project_owner_agent.approval import ApprovalMode, TerminalApproval
from agentplanex.project_owner_agent.exception import AgentFlowExit
from agentplanex.project_owner_agent.interactive import InteractiveAgent
from agentplanex.project_owner_agent.models.jbb import JBBModel
from agentplanex.project_owner_agent.tools import ToolCatalog
from agentplanex.services.event_bus import EventBus
from agentplanex.services.planning import PlanningService
from agentplanex.services.runtime_context import RuntimeContextService
from agentplanex.settings import Settings

DEFAULT_SYSTEM_PROMPT = (
    "You are a Project Owner Agent working in a local repository. "
    "Use Bash when needed and return a concise final response."
)


@dataclass(frozen=True, slots=True)
class _OwnerSession:
    context: ProjectRuntimeContext
    agent: DefaultAgent


@dataclass(slots=True)
class ProjectRuntimeService:
    """Create, restore, and retain one Project Owner session."""

    project_path: Path
    settings: Settings
    approval_mode: ApprovalMode
    tools: ToolCatalog
    execute_tool: ToolExecutor
    planning: PlanningService
    event_bus: EventBus
    runtime_contexts: RuntimeContextService
    contexts: SQLiteProjectRuntimeContextRepository = field(
        default_factory=SQLiteProjectRuntimeContextRepository
    )
    owners: SQLiteProjectOwnerAgentRepository = field(
        default_factory=SQLiteProjectOwnerAgentRepository
    )
    messages: SQLiteMessageHistoryRepository = field(
        default_factory=SQLiteMessageHistoryRepository
    )
    _database: SQLiteDatabase = field(init=False, repr=False)
    _session: _OwnerSession | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.approval_mode not in {"confirm", "yolo"}:
            raise ValueError(f"Unknown approval mode: {self.approval_mode!r}")
        self._database = SQLiteDatabase.for_project(self.project_path)

    def run(self, task: ProjectOwnerTask) -> AgentExit:
        """Persist one typed input and run a Project Owner React Loop."""
        try:
            self._prepare_task(task)
            return self._run_prepared(task.type)
        except Exception as error:
            return AgentExit(
                status=AgentExitStatus.UNHANDLED_EXCEPTION,
                content=f"{type(error).__name__}: {error}",
            )

    def _run_prepared(self, task_type: ProjectOwnerTaskType) -> AgentExit:
        session = self._session
        if session is None:
            raise RuntimeError("Project Owner task was not prepared")

        triage_id = session.context.triage_id
        react_loop_id = uuid4().hex
        self.event_bus.publish(
            ExecutionEvent(
                triage_id=triage_id,
                event_type=ExecutionEventType.REACT_LOOP_ENTERED,
                react_loop_id=react_loop_id,
                payload={"task_type": task_type.value},
            )
        )

        try:
            self._mark_conversation_started()
            session = self._session
            if session is None:
                raise RuntimeError("Project Owner session was lost before execution")
            session.agent.run(session.context)
        except AgentFlowExit as error:
            result = AgentExit(status=error.status, content=error.content)
        except Exception as error:
            result = AgentExit(
                status=AgentExitStatus.UNHANDLED_EXCEPTION,
                content=f"{type(error).__name__}: {error}",
            )
        self.event_bus.publish(
            ExecutionEvent(
                triage_id=triage_id,
                event_type=ExecutionEventType.REACT_LOOP_EXITED,
                react_loop_id=react_loop_id,
                payload={"agent_exit_status": result.status.value},
            )
        )
        return result

    def execute_action(self, action: Action) -> ToolExecutionResult:
        """Execute one explicit action against the persisted project context."""
        context, _messages = self._load_or_create_state()
        return self.execute_tool(context, action)

    def interact(
        self,
        *,
        action: UserInteractionAction = "message",
        message: str = "",
    ) -> AgentExit:
        """Apply one user interaction and resume the Project Owner."""
        if action not in {"message", "approve", "reject"}:
            return self._interaction_error(
                f"Unsupported user interaction: {action!r}"
            )
        if action == "message":
            if not message.strip():
                return self._interaction_error("User message must not be empty")
            return self.run(
                ProjectOwnerTask(
                    type=ProjectOwnerTaskType.USER_INPUT,
                    content=message.strip(),
                )
            )

        try:
            task = ProjectOwnerTask(
                type=ProjectOwnerTaskType.USER_INPUT,
                content=(
                    "The user approved the current Plan."
                    if action == "approve"
                    else self._plan_rejection_message(message)
                ),
            )
            self._prepare_task(task)
            session = self._session
            if session is None:
                raise RuntimeError("Project Owner task was not prepared")
            decision = (
                self.planning.approve_plan(session.context.triage_id)
                if action == "approve"
                else self.planning.reject_plan(session.context.triage_id)
            )
            self._replace_session_context(decision.context)
        except Exception as error:
            return self._interaction_error(f"{type(error).__name__}: {error}")

        return self._run_prepared(task.type)

    @staticmethod
    def _interaction_error(content: str) -> AgentExit:
        return AgentExit(
            status=AgentExitStatus.UNHANDLED_EXCEPTION,
            content=content,
        )

    def _load_session(self) -> _OwnerSession:
        context, messages = self._load_or_create_state()
        owner = context.project_owner_agent
        if owner is None:
            raise RuntimeError("Project Runtime Service returned no Project Owner Agent")

        expected_tools = tuple(tool.name for tool in self.tools.tools)
        if owner.tools != expected_tools:
            raise ValueError(f"Unsupported persisted Project Owner tools: {owner.tools!r}")

        owner_settings = self.settings.project_owner_agent
        model_settings = owner_settings.model
        model = JBBModel(
            model=model_settings.name,
            tools=self.tools,
            base_url=model_settings.base_url,
            timeout_seconds=model_settings.timeout_seconds,
        )
        config = AgentConfig(
            system_prompt=owner.system_prompt,
            step_limit=owner_settings.step_limit,
            max_consecutive_format_errors=(
                owner_settings.max_consecutive_format_errors
            ),
        )
        agent = (
            DefaultAgent(
                model,
                self.execute_tool,
                append_messages=self.append_messages,
                initial_messages=messages,
                config=config,
            )
            if self.approval_mode == "yolo"
            else InteractiveAgent(
                model,
                self.execute_tool,
                append_messages=self.append_messages,
                initial_messages=list(messages),
                approval=TerminalApproval(
                    require_tty=os.getenv(
                        "AGENTPLANEX_REQUIRE_INTERACTIVE_TERMINAL", "1"
                    )
                    != "0"
                ),
                config=config,
            )
        )
        return _OwnerSession(context=context, agent=agent)

    def _prepare_task(self, task: ProjectOwnerTask) -> None:
        content = task.content.strip()
        if not content:
            raise ValueError("Project Owner task content must not be empty")
        if self._session is None:
            self._session = self._load_session()
        session = self._session
        initial: list[Message] = []
        if not session.agent.messages:
            initial.append(
                {"role": "system", "content": session.agent.config.system_prompt}
            )
        initial.append({"role": "user", "content": content})
        session.agent.add_messages(session.context, *initial)

    def _mark_conversation_started(self) -> None:
        session = self._session
        if session is None:
            raise RuntimeError("Project Owner session is not loaded")

        def start(current: ProjectRuntimeContext) -> ProjectRuntimeContext:
            return (
                replace(current, status="TODO")
                if current.status == "TRIAGE"
                else current
            )

        updated = self.runtime_contexts.transition(
            session.context.triage_id,
            reason=RuntimeContextChangeReason.CONVERSATION_STARTED,
            mutate=start,
        )
        self._replace_session_context(updated)

    def _replace_session_context(self, context: ProjectRuntimeContext) -> None:
        session = self._session
        if session is None:
            raise RuntimeError("Project Owner session is not loaded")
        self._session = _OwnerSession(
            context=replace(
                context,
                project_owner_agent=session.context.project_owner_agent,
            ),
            agent=session.agent,
        )

    @staticmethod
    def _plan_rejection_message(feedback: str) -> str:
        message = "The user rejected the current Plan."
        return f"{message} Feedback: {feedback.strip()}" if feedback.strip() else message

    def _load_or_create_state(
        self,
    ) -> tuple[ProjectRuntimeContext, tuple[Message, ...]]:
        initialize_schema(self._database)
        tool_names = tuple(tool.name for tool in self.tools.tools)

        with self._database.transaction() as connection:
            existing_contexts = self.contexts.list_all(connection)
            if len(existing_contexts) > 1:
                raise ValueError(
                    "Project contains more than one Project Runtime context"
                )
            if existing_contexts:
                context = existing_contexts[0]
            else:
                context = ProjectRuntimeContext(triage_id=uuid4().hex)
                self.contexts.insert(connection, context)

            owner = self.owners.get_by_triage_id(connection, context.triage_id)
            if owner is None:
                owner = ProjectOwnerAgent(
                    triage_id=context.triage_id,
                    project_owner_session_id=uuid4().hex,
                    system_prompt=DEFAULT_SYSTEM_PROMPT,
                    tools=tool_names,
                )
                self.owners.insert(connection, owner)

        loaded_context = replace(context, project_owner_agent=owner)
        return loaded_context, self._load_messages(owner)

    def append_messages(
        self,
        context: ProjectRuntimeContext,
        appended: tuple[Message, ...],
    ) -> None:
        """Atomically append messages and advance the Owner's latest pointer."""
        if not appended:
            return
        loaded_owner = context.project_owner_agent
        if loaded_owner is None:
            raise ValueError("Project Runtime Context has no Project Owner Agent")

        with self._database.transaction() as connection:
            owner = self.owners.get_by_session_id(
                connection,
                loaded_owner.project_owner_session_id,
            )
            if owner is None:
                raise LookupError(
                    "Project Owner Agent not found: "
                    f"{loaded_owner.project_owner_session_id}"
                )

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

    def _load_messages(self, owner: ProjectOwnerAgent) -> tuple[Message, ...]:
        with self._database.connection() as connection:
            histories = self.messages.list_by_session_id(
                connection,
                owner.project_owner_session_id,
            )

        latest_id = histories[-1].message_id if histories else None
        if latest_id != owner.message_id:
            raise RuntimeError(
                "Project Owner Agent latest message pointer does not match message history"
            )
        return tuple(message for history in histories for message in history.message)
