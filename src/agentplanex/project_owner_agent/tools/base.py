"""Model-facing tool catalog for the Project Owner Agent."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    ConfigDict,
    StringConstraints,
    ValidationError,
)

from agentplanex.project_owner_agent.contracts import (
    Action,
    ToolArguments,
    ToolSchema,
)


class ToolArgumentError(ValueError):
    """A model-proposed Tool Call does not satisfy its argument contract."""


class ToolArgumentsModel(BaseModel):
    """Strict base for arguments shared by provider schemas and Runtime parsing."""

    model_config = ConfigDict(extra="forbid", strict=True)


class NoToolArguments(ToolArgumentsModel):
    """The explicit empty-object contract for a Tool without arguments."""


type NonBlankText = Annotated[
    str,
    StringConstraints(min_length=1, pattern=r"\S"),
]
type ToolIdentifier = Annotated[
    str,
    StringConstraints(min_length=1, pattern=r"^[A-Za-z0-9._-]+$"),
]

@dataclass(frozen=True, slots=True)
class ToolDefinition[ArgumentsT: ToolArgumentsModel]:
    """One source for a Tool's model schema and Runtime argument parser."""

    name: str
    description: str
    arguments_type: type[ArgumentsT]

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("tool name must not be empty")
        if not self.description.strip():
            raise ValueError(f"tool description must not be empty: {self.name!r}")

    def parse_arguments(self, arguments: object) -> ArgumentsT:
        """Validate arguments before a Tool Call can execute."""
        try:
            return self.arguments_type.model_validate(arguments)
        except ValidationError as error:
            details = "; ".join(
                f"{'.'.join(str(part) for part in issue['loc'])}: {issue['msg']}"
                for issue in error.errors()
            )
            raise ToolArgumentError(
                f"Invalid arguments for {self.name}: {details}"
            ) from error

    def provider_schema(self) -> ToolSchema:
        """Render the provider schema from the same contract used at Runtime."""
        parameters = self.arguments_type.model_json_schema()
        parameters.setdefault("required", [])
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": parameters,
            "strict": True,
        }


@dataclass(frozen=True, slots=True, init=False)
class ToolCatalog:
    """Expose schemas and validate model-proposed tool calls."""

    tools: tuple[ToolDefinition[Any], ...]

    def __init__(self, tools: Sequence[ToolDefinition[Any]]) -> None:
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
            schemas.append(tool.provider_schema())
        return schemas

    def create_action(
        self,
        *,
        name: str,
        call_id: str,
        arguments: ToolArguments,
    ) -> Action:
        definition = self._get(name)
        parsed = definition.parse_arguments(arguments)
        return {
            "tool": name,
            "call_id": call_id,
            "arguments": dict(parsed.model_dump(mode="python")),
        }

    def select(self, names: Sequence[str]) -> "ToolCatalog":
        """Return the persisted ordered capability contract for one Owner."""

        selected: list[ToolDefinition[Any]] = []
        for name in names:
            selected.append(self._get(name))
        return ToolCatalog(selected)

    def _get(self, name: str) -> ToolDefinition[Any]:
        for tool in self.tools:
            if tool.name == name:
                return tool
        raise ValueError(f"Unknown tool: {name!r}")
