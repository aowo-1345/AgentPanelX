"""Application composition for a project-scoped Runtime."""

from pathlib import Path

from agentplanex.project_owner_agent.approval import ApprovalMode
from agentplanex.project_runtime import ProjectRuntime
from agentplanex.settings import load_settings


def create_project_runtime(
    *,
    project_path: Path,
    approval_mode: ApprovalMode,
) -> ProjectRuntime:
    """Create a Runtime from explicit invocation inputs and loaded settings."""
    return ProjectRuntime(
        project_path=project_path,
        settings=load_settings(),
        approval_mode=approval_mode,
    )
