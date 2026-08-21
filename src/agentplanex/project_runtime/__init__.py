"""Project-scoped Runtime command boundary."""

from typing import TYPE_CHECKING, Any

from agentplanex.project_runtime.errors import FeatureBusyError

if TYPE_CHECKING:
    from agentplanex.project_runtime.runtime import ProjectRuntime

__all__ = ["FeatureBusyError", "ProjectRuntime"]


def __getattr__(name: str) -> Any:
    """Load the facade lazily so inner Services can import Runtime errors."""
    if name == "ProjectRuntime":
        from agentplanex.project_runtime.runtime import ProjectRuntime

        return ProjectRuntime
    raise AttributeError(name)
