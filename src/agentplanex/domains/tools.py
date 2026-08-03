"""Shared tool calling contracts."""

from collections.abc import Callable
from typing import Any

from agentplanex.domains.project_runtime_context import ProjectRuntimeContext

type ToolArguments = dict[str, Any]
type ToolSchema = dict[str, Any]
type Action = dict[str, Any]
type ActionOutput = dict[str, Any]
type ToolExecutor = Callable[[ProjectRuntimeContext, Action], ActionOutput]

BASH_TOOL_NAME = "bash"
