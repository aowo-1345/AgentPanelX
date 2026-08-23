"""Private two-phase composition for one Feature Runtime Context."""

from dataclasses import dataclass, field
from pathlib import Path

from agentplanex.infrastructure.sqlite import SQLiteDatabase
from agentplanex.project_owner_agent.approval import ApprovalMode
from agentplanex.project_owner_agent.models.responses import ResponsesClient
from agentplanex.project_owner_agent.tools import ToolCatalog
from agentplanex.services.agent_invocation import AgentPromptCatalog
from agentplanex.services.event_bus import EventBus
from agentplanex.services.project_runtime_context._owner import _OwnerRuntime
from agentplanex.services.project_runtime_context.context import (
    MutationFenceGuard,
    ProjectRuntimeContext,
    _allow_mutation,
)
from agentplanex.services.project_runtime_context.contracts import RuntimeToolExecutor
from agentplanex.settings import Settings


@dataclass(slots=True)
class _ProjectRuntimeContextAssembly:
    """Own the private bindings that close the Runtime Context cycle."""

    context: ProjectRuntimeContext
    _settings: Settings
    _approval_mode: ApprovalMode
    _responses: ResponsesClient
    _observation_skill: Path
    _prompts: AgentPromptCatalog
    _completed: bool = field(default=False, init=False)

    def complete(
        self,
        *,
        tools: ToolCatalog,
        tool_executor: RuntimeToolExecutor,
    ) -> ProjectRuntimeContext:
        """Bind all private dependencies and seal the Context exactly once."""
        if self._completed:
            raise RuntimeError("Project Runtime Context assembly is already complete")

        owner = _OwnerRuntime(
            database=self.context.database,
            settings=self._settings,
            approval_mode=self._approval_mode,
            tools=tools,
            tool_executor=tool_executor,
            event_bus=self.context.event_bus,
            responses=self._responses,
            observation_skill=self._observation_skill,
            prompts=self._prompts,
            load_state=self.context._reload_state,
            set_activation_initial_summary=(self.context._set_owner_activation_initial_summary),
        )
        self.context._complete(
            owner_runtime=owner,
            tool_executor=tool_executor,
        )
        self._completed = True
        return self.context


def prepare_project_runtime_context(
    *,
    project_path: Path,
    database: SQLiteDatabase,
    event_bus: EventBus,
    settings: Settings,
    approval_mode: ApprovalMode,
    responses: ResponsesClient,
    observation_skill: Path,
    prompts: AgentPromptCatalog,
    mutation_fence_guard: MutationFenceGuard = _allow_mutation,
) -> _ProjectRuntimeContextAssembly:
    """Prepare an incomplete Context for dependency graph construction."""
    return _ProjectRuntimeContextAssembly(
        context=ProjectRuntimeContext(
            project_path=project_path,
            database=database,
            event_bus=event_bus,
            mutation_fence_guard=mutation_fence_guard,
        ),
        _settings=settings,
        _approval_mode=approval_mode,
        _responses=responses,
        _observation_skill=observation_skill,
        _prompts=prompts,
    )
