"""Project tool execution registration and dispatch."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from agentplanex.domains import (
    Action,
    ActionOutput,
    ProjectRuntimeContext,
    ToolArguments,
)
from agentplanex.project_owner_agent.tools import ToolCatalog, ToolDefinition

type ExecutionHandler = Callable[
    [ProjectRuntimeContext, ToolArguments], ActionOutput
]


@dataclass(frozen=True, slots=True)
class ProjectExecution:
    definition: ToolDefinition
    handler: ExecutionHandler


@dataclass(frozen=True, slots=True, init=False)
class ProjectExecutions:
    """Expose model tools and dispatch their project-bound executions."""

    tools: ToolCatalog
    _handlers: dict[str, ExecutionHandler]

    def __init__(self, executions: Sequence[ProjectExecution]) -> None:
        registered = tuple(executions)
        tools = ToolCatalog([execution.definition for execution in registered])
        object.__setattr__(self, "tools", tools)
        object.__setattr__(
            self,
            "_handlers",
            {
                execution.definition.name: execution.handler
                for execution in registered
            },
        )

    def execute(
        self,
        context: ProjectRuntimeContext,
        action: Action,
    ) -> ActionOutput:
        tool_name = action.get("tool")
        if not isinstance(tool_name, str) or not tool_name:
            return _invalid_action("Tool action has no tool name")

        arguments = action.get("arguments")
        if not isinstance(arguments, dict):
            return _invalid_action(f"Tool {tool_name!r} arguments must be an object")

        handler = self._handlers.get(tool_name)
        if handler is None:
            return _invalid_action(f"Unknown tool: {tool_name!r}")
        return handler(context, arguments)


def _invalid_action(message: str) -> ActionOutput:
    return {
        "output": "",
        "returncode": -1,
        "exception_info": message,
    }
