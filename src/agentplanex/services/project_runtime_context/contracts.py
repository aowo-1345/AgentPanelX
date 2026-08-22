"""Execution contract consumed by the Project Runtime Context."""

from collections.abc import Callable

from agentplanex.domains.project_runtime_state import ProjectRuntimeState
from agentplanex.project_owner_agent.contracts import Action, ToolExecutionResult

type RuntimeToolExecutor = Callable[
    [ProjectRuntimeState, Action], ToolExecutionResult
]
