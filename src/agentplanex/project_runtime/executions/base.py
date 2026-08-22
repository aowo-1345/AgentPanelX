"""Project tool execution registration and dispatch."""

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, cast

from agentplanex.domains.project_runtime_state import ProjectRuntimeState
from agentplanex.project_owner_agent.contracts import Action, ToolExecutionResult
from agentplanex.project_owner_agent.tools import (
    ToolArgumentError,
    ToolArgumentsModel,
    ToolCatalog,
    ToolDefinition,
)
from agentplanex.services.agent_collaboration import AgentCollaborationService
from agentplanex.services.delivery._service import DeliveryService
from agentplanex.services.event_bus import EventBus
from agentplanex.services.planning._service import PlanningService
from agentplanex.services.project_runtime_context.context import ProjectRuntimeContext
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
    context: ProjectRuntimeContext


class ProjectExecution[ArgumentsT: ToolArgumentsModel](ABC):
    """One model-visible tool bound to a project runtime."""

    definition: ClassVar[ToolDefinition[Any]]

    def __init__(self, dependencies: ProjectExecutionDependencies) -> None:
        self.dependencies = dependencies

    def tool_definition(self) -> ToolDefinition[ArgumentsT]:
        """Return this Runtime instance's model-visible tool definition."""
        return cast(ToolDefinition[ArgumentsT], self.definition)

    def execute_call(
        self,
        context: ProjectRuntimeState,
        raw_arguments: object,
    ) -> ToolExecutionResult:
        """Parse and execute one call through this Tool's sole argument contract."""
        return self.execute(
            context,
            self.tool_definition().parse_arguments(raw_arguments),
        )

    @abstractmethod
    def execute(
        self,
        context: ProjectRuntimeState,
        arguments: ArgumentsT,
    ) -> ToolExecutionResult:
        """Execute one validated tool action."""


_execution_types: dict[str, type[ProjectExecution[Any]]] = {}
_FIXED_TOOL_ORDER = (
    "bash",
    "request_plan_approval",
    "talk_to_agent",
    "update_milestones",
    "run_next_milestone",
    "decide_milestone_candidate",
)


def project_execution(
    definition: ToolDefinition[Any],
) -> Callable[
    [type[ProjectExecution[Any]]],
    type[ProjectExecution[Any]],
]:
    """Register one project execution class for a model-visible tool."""

    def register(
        execution_type: type[ProjectExecution[Any]],
    ) -> type[ProjectExecution[Any]]:
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
    _executions: dict[str, ProjectExecution[Any]]
    _context: ProjectRuntimeContext

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
        object.__setattr__(self, "_context", dependencies.context)

    def execute(
        self,
        context: ProjectRuntimeState,
        action: Action,
    ) -> ToolExecutionResult:
        tool_name = action.get("tool")
        if not isinstance(tool_name, str) or not tool_name:
            return _invalid_action("Tool action has no tool name")

        current = self._context.state()
        if current.triage_id != context.triage_id:
            raise ValueError("Tool State does not belong to this Project Runtime")
        if current.blocked_reason is not None:
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
        try:
            return execution.execute_call(current, arguments)
        except ToolArgumentError as error:
            return ToolExecutionResult(
                output={
                    "ok": False,
                    "error": {
                        "code": "INVALID_TOOL_ARGUMENTS",
                        "message": str(error),
                        "retryable": True,
                    },
                }
            )


def _invalid_action(message: str) -> ToolExecutionResult:
    return ToolExecutionResult(
        output={
            "ok": False,
            "error": {
                "code": "INVALID_TOOL_CALL",
                "message": message,
                "retryable": True,
            },
        }
    )


def _tool_position(name: str) -> int:
    try:
        return _FIXED_TOOL_ORDER.index(name)
    except ValueError as error:
        raise ValueError(f"Tool is not in the fixed Runtime catalog: {name!r}") from error
