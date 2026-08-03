"""Long-lived Project Owner session and persistence service."""

import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from uuid import uuid4

from agentplanex.domains import (
    AgentExit,
    AgentExitStatus,
    Message,
    MessageHistory,
    ProjectOwnerAgent,
    ProjectRuntimeContext,
    ToolExecutor,
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

    def run(self, task: str = "") -> AgentExit:
        """Run one turn, restoring the Owner only on first use."""
        try:
            if self._session is None:
                self._session = self._load_session()
            self._session.agent.run(self._session.context, task)
        except AgentFlowExit as error:
            return AgentExit(status=error.status, content=error.content)
        except Exception as error:
            return AgentExit(
                status=AgentExitStatus.UNHANDLED_EXCEPTION,
                content=f"{type(error).__name__}: {error}",
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
