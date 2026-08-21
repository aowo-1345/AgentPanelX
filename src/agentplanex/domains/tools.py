"""Shared tool calling contracts."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from agentplanex.domains.agent_exit import AgentExit
from agentplanex.domains.project_runtime_state import ProjectRuntimeState

type ToolArguments = dict[str, Any]
type ToolSchema = dict[str, Any]
type Action = dict[str, Any]
type ActionOutput = dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolExecutionResult:
    output: ActionOutput
    exit: AgentExit | None = None


type ToolExecutor = Callable[
    [ProjectRuntimeState, Action], ToolExecutionResult
]

BASH_TOOL_NAME = "bash"
