"""Application composition for a project-scoped Runtime."""

from pathlib import Path

from agentplanex.infrastructure.openai_responses import OpenAIResponsesTransport
from agentplanex.infrastructure.workspace_git import WorkspaceGit
from agentplanex.infrastructure.workspace_registry import WorkspaceRegistry
from agentplanex.project_owner_agent.approval import ApprovalMode
from agentplanex.project_owner_agent.models.responses import ResponsesTransport
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
    responses_transport: ResponsesTransport | None = None,
) -> ProjectRuntime:
    """Create a Runtime from explicit invocation inputs and loaded settings."""
    configured = settings or load_settings()
    return ProjectRuntime(
        project_path=project_path,
        settings=configured,
        approval_mode=approval_mode,
        responses_transport=(
            responses_transport
            if responses_transport is not None
            else _create_responses_transport(configured)
        ),
    )


def create_workspace(settings: Settings) -> WorkspaceService:
    """Compose the user-level Workspace over real Registry, Git, and Runtimes."""
    responses_transport = _create_responses_transport(settings)
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
            responses_transport=responses_transport,
        ),
        close_resources=responses_transport.close,
    )


def _create_responses_transport(settings: Settings) -> OpenAIResponsesTransport:
    model = settings.project_owner_agent.selected_model
    return OpenAIResponsesTransport(
        base_url=model.base_url,
        timeout_seconds=model.timeout_seconds,
        api_key_env=model.api_key_env,
        http_headers=model.http_headers,
        reasoning_effort=model.reasoning_effort,
        service_tier=model.service_tier,
    )
