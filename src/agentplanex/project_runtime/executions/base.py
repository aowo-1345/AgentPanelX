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
from agentplanex.services.planning import PlanningService
from agentplanex.settings import RuntimeSettings


@dataclass(frozen=True, slots=True)
class ProjectExecutionDependencies:
    """Stable dependencies shared by project-bound tool executions."""

    project_path: Path
    settings: RuntimeSettings
    planning: PlanningService


class ProjectExecution(ABC):
    """One model-visible tool bound to a project runtime."""

    definition: ClassVar[ToolDefinition]

    def __init__(self, dependencies: ProjectExecutionDependencies) -> None:
        self.dependencies = dependencies

    @abstractmethod
    def execute(
        self,
        context: ProjectRuntimeContext,
        arguments: ToolArguments,
    ) -> ToolExecutionResult:
        """Execute one validated tool action."""


_execution_types: dict[str, type[ProjectExecution]] = {}


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

    def __init__(self, dependencies: ProjectExecutionDependencies) -> None:
        executions = tuple(
            execution_type(dependencies)
            for execution_type in _execution_types.values()
        )
        object.__setattr__(
            self,
            "tools",
            ToolCatalog([execution.definition for execution in executions]),
        )
        object.__setattr__(
            self,
            "_executions",
            {
                execution.definition.name: execution
                for execution in executions
            },
        )

    def execute(
        self,
        context: ProjectRuntimeContext,
        action: Action,
    ) -> ToolExecutionResult:
        tool_name = action.get("tool")
        if not isinstance(tool_name, str) or not tool_name:
            return _invalid_action("Tool action has no tool name")

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
