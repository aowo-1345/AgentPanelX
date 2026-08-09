"""Project tool execution registration and dispatch."""

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from agentplanex.domains import (
    Action,
    ProjectRuntimeContext,
    ToolArguments,
    ToolExecutionResult,
)
from agentplanex.project_owner_agent.tools import ToolCatalog, ToolDefinition
from agentplanex.services.agent_collaboration import AgentCollaborationService
from agentplanex.services.delivery import DeliveryService
from agentplanex.services.event_bus import EventBus
from agentplanex.services.planning import PlanningService
from agentplanex.services.runtime_context import RuntimeContextService
from agentplanex.settings import RuntimeSettings


@dataclass(frozen=True, slots=True)
class ProjectExecutionDependencies:
    """Stable dependencies shared by project-bound tool executions."""

    project_path: Path
    settings: RuntimeSettings
    planning: PlanningService
    delivery: DeliveryService
    collaboration: AgentCollaborationService
    event_bus: EventBus
    runtime_contexts: RuntimeContextService


class ProjectExecution(ABC):
    """One model-visible tool bound to a project runtime."""

    definition: ClassVar[ToolDefinition]

    def __init__(self, dependencies: ProjectExecutionDependencies) -> None:
        self.dependencies = dependencies

    def tool_definition(self) -> ToolDefinition:
        """Return this Runtime instance's model-visible tool definition."""
        return self.definition

    @abstractmethod
    def execute(
        self,
        context: ProjectRuntimeContext,
        arguments: ToolArguments,
    ) -> ToolExecutionResult:
        """Execute one validated tool action."""


_execution_types: dict[str, type[ProjectExecution]] = {}
_FIXED_TOOL_ORDER = (
    "bash",
    "request_plan_approval",
    "talk_to_agent",
    "update_milestones",
    "run_next_milestone",
    "decide_milestone_candidate",
)


def project_execution(
    definition: ToolDefinition,
) -> Callable[[type[ProjectExecution]], type[ProjectExecution]]:
    """Register one project execution class for a model-visible tool."""

    def register(
        execution_type: type[ProjectExecution],
    ) -> type[ProjectExecution]:
        existing = _execution_types.get(definition.name)
        if existing is not None and existing is not execution_type:
            raise ValueError(f"Duplicate project execution: {definition.name!r}")
        execution_type.definition = definition
        _execution_types[definition.name] = execution_type
        return execution_type

    return register


@dataclass(frozen=True, slots=True, init=False)
class ProjectExecutions:
    """Expose registered tools and dispatch their project-bound executions."""

    tools: ToolCatalog
    _executions: dict[str, ProjectExecution]
    _runtime_contexts: RuntimeContextService

    def __init__(self, dependencies: ProjectExecutionDependencies) -> None:
        executions = tuple(
            execution_type(dependencies)
            for _name, execution_type in sorted(
                _execution_types.items(),
                key=lambda item: _tool_position(item[0]),
            )
        )
        object.__setattr__(
            self,
            "tools",
            ToolCatalog([execution.tool_definition() for execution in executions]),
        )
        object.__setattr__(
            self,
            "_executions",
            {
                execution.tool_definition().name: execution
                for execution in executions
            },
        )
        object.__setattr__(self, "_runtime_contexts", dependencies.runtime_contexts)

    def execute(
        self,
        context: ProjectRuntimeContext,
        action: Action,
    ) -> ToolExecutionResult:
        tool_name = action.get("tool")
        if not isinstance(tool_name, str) or not tool_name:
            return _invalid_action("Tool action has no tool name")

        current = self._runtime_contexts.get(context.triage_id)
        if current is not None and current.blocked_reason is not None:
            return ToolExecutionResult(
                output={
                    "ok": False,
                    "error_type": "USER_INTERVENTION_REQUIRED",
                    "blocked_capability": current.blocked_capability,
                    "reason": current.blocked_reason,
                    "guidance": (
                        "Do not call another tool or attempt to bypass the sandbox. "
                        "Explain the blocker and required user action, then return "
                        "control to the user."
                    ),
                }
            )

        arguments = action.get("arguments")
        if not isinstance(arguments, dict):
            return _invalid_action(f"Tool {tool_name!r} arguments must be an object")

        execution = self._executions.get(tool_name)
        if execution is None:
            return _invalid_action(f"Unknown tool: {tool_name!r}")
        return execution.execute(context, arguments)


def _invalid_action(message: str) -> ToolExecutionResult:
    return ToolExecutionResult(
        output={
            "output": "",
            "returncode": -1,
            "exception_info": message,
        }
    )


def _tool_position(name: str) -> int:
    try:
        return _FIXED_TOOL_ORDER.index(name)
    except ValueError as error:
        raise ValueError(f"Tool is not in the fixed Runtime catalog: {name!r}") from error
