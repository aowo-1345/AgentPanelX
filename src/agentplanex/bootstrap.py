"""Application composition for a project-scoped Runtime."""

from pathlib import Path

from agentplanex.infrastructure.workspace_git import WorkspaceGit
from agentplanex.infrastructure.workspace_registry import WorkspaceRegistry
from agentplanex.project_owner_agent.approval import ApprovalMode
from agentplanex.project_runtime import ProjectRuntime
from agentplanex.services.workspace.dispatcher import WorkspaceDispatcher
from agentplanex.services.workspace.queries import WorkspaceQueries
from agentplanex.services.workspace.service import WorkspaceService
from agentplanex.settings import Settings, load_settings


def create_project_runtime(
    *,
    project_path: Path,
    approval_mode: ApprovalMode,
    settings: Settings | None = None,
) -> ProjectRuntime:
    """Create a Runtime from explicit invocation inputs and loaded settings."""
    return ProjectRuntime(
        project_path=project_path,
        settings=settings or load_settings(),
        approval_mode=approval_mode,
    )


def create_workspace(settings: Settings) -> WorkspaceService:
    """Compose the user-level Workspace over real Registry, Git, and Runtimes."""
    registry = WorkspaceRegistry.at(settings.workspace.data_home / "registry.sqlite3")
    registry.initialize()
    git = WorkspaceGit()
    return WorkspaceService(
        data_home=settings.workspace.data_home,
        registry=registry,
        git=git,
        queries=WorkspaceQueries(registry=registry, git=git),
        dispatcher=WorkspaceDispatcher(
            max_parallel_features=settings.workspace.max_parallel_features
        ),
        runtime_factory=lambda project_path: create_project_runtime(
            project_path=project_path,
            approval_mode="yolo",
            settings=settings,
        ),
    )
