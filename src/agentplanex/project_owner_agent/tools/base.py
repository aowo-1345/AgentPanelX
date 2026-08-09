"""Model-facing tool catalog for the Project Owner Agent."""

from collections.abc import Sequence
from dataclasses import dataclass

from agentplanex.domains import Action, ToolArguments, ToolSchema


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """One model-visible tool name and provider schema."""

    name: str
    schema: ToolSchema

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("tool name must not be empty")
        if self.schema.get("name") != self.name:
            raise ValueError(f"schema name does not match tool {self.name!r}")


@dataclass(frozen=True, slots=True, init=False)
class ToolCatalog:
    """Expose schemas and validate model-proposed tool calls."""

    tools: tuple[ToolDefinition, ...]

    def __init__(self, tools: Sequence[ToolDefinition]) -> None:
        registered = tuple(tools)
        if not registered:
            raise ValueError("at least one tool must be registered")

        names = [tool.name for tool in registered]
        if any(not name.strip() for name in names):
            raise ValueError("tool names must not be empty")
        if len(names) != len(set(names)):
            raise ValueError("tool names must be unique")

        object.__setattr__(self, "tools", registered)

    def provider_schemas(self) -> list[ToolSchema]:
        schemas: list[ToolSchema] = []
        for tool in self.tools:
            schemas.append(tool.schema)
        return schemas

    def create_action(
        self,
        *,
        name: str,
        call_id: str,
        arguments: ToolArguments,
    ) -> Action:
        self._get(name)
        return {
            "tool": name,
            "call_id": call_id,
            "arguments": arguments,
        }

    def select(self, names: Sequence[str]) -> "ToolCatalog":
        """Return the persisted ordered capability contract for one Owner."""

        selected: list[ToolDefinition] = []
        for name in names:
            selected.append(self._get(name))
        return ToolCatalog(selected)

    def _get(self, name: str) -> ToolDefinition:
        for tool in self.tools:
            if tool.name == name:
                return tool
        raise ValueError(f"Unknown tool: {name!r}")
