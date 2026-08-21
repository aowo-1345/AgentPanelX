"""Stable Workspace scheduling failures exposed to external adapters."""

from agentplanex.services.project_runtime_error import ProjectRuntimeCommandError


class WorkspaceSchedulingError(ProjectRuntimeCommandError):
    """A Workspace command was rejected before it changed Runtime state."""

    code: str


class WorkspaceCapacityExhaustedError(WorkspaceSchedulingError):
    code = "WORKSPACE_CAPACITY_EXHAUSTED"

    def __init__(self, max_parallel_features: int) -> None:
        super().__init__(
            "Workspace automatic execution capacity is exhausted "
            f"({max_parallel_features} active Feature slots)"
        )
