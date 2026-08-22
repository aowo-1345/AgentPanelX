"""Application composition for a project-scoped Runtime."""

from pathlib import Path

from agentplanex.infrastructure.git_repository import GitRepository
from agentplanex.infrastructure.logging import configure_logging
from agentplanex.infrastructure.model_gateway import (
    ModelGateway,
    OpenAIResponsesAdapter,
    QwenResponsesAdapter,
)
from agentplanex.infrastructure.sqlite import SQLiteDatabase
from agentplanex.infrastructure.workspace_git import WorkspaceGit
from agentplanex.infrastructure.workspace_registry import WorkspaceRegistry
from agentplanex.project_owner_agent.approval import ApprovalMode
from agentplanex.project_owner_agent.models.responses import ResponsesTransport
from agentplanex.project_runtime import ProjectRuntime
from agentplanex.project_runtime.composition import (
    compose_project_runtime,
    compose_project_runtime_control,
)
from agentplanex.project_runtime.control import ProjectRuntimeControl
from agentplanex.services.project_control import ProjectControlQuery
from agentplanex.services.web import ProjectWorkspaceQuery
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
    configure_logging()
    configured = settings or load_settings()
    return compose_project_runtime(
        project_path=project_path,
        settings=configured,
        approval_mode=approval_mode,
        responses_transport=(
            responses_transport
            if responses_transport is not None
            else create_responses_transport(configured)
        ),
    )


def create_project_runtime_control(
    *,
    project_path: Path,
    approval_mode: ApprovalMode,
    settings: Settings | None = None,
    responses_transport: ResponsesTransport | None = None,
) -> ProjectRuntimeControl:
    """Create the privileged intervention surface over the real command graph."""
    configure_logging()
    configured = settings or load_settings()
    return compose_project_runtime_control(
        project_path=project_path,
        settings=configured,
        approval_mode=approval_mode,
        responses_transport=(
            responses_transport
            if responses_transport is not None
            else create_responses_transport(configured)
        ),
    )


def create_project_control_query(*, project_path: Path) -> ProjectControlQuery:
    """Create a read-only projection without constructing a Runtime command graph."""
    resolved = project_path.resolve()
    if not resolved.is_dir():
        raise ValueError(f"Project path is not a directory: {resolved}")
    return ProjectControlQuery(
        database=SQLiteDatabase.for_project(resolved),
        git=GitRepository(resolved),
    )


def create_project_workspace_query(*, project_path: Path) -> ProjectWorkspaceQuery:
    """Create the read-only Web/CLI projection without a command graph."""
    resolved = project_path.resolve()
    if not resolved.is_dir():
        raise ValueError(f"Project path is not a directory: {resolved}")
    return ProjectWorkspaceQuery(
        database=SQLiteDatabase.for_project(resolved),
        git=GitRepository(resolved),
    )


def create_workspace(settings: Settings) -> WorkspaceService:
    """Compose the user-level Workspace over real Registry, Git, and Runtimes."""
    configure_logging()
    responses_transport = create_responses_transport(settings)
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


def create_responses_transport(settings: Settings) -> ModelGateway:
    """Bind the selected model Adapter to one application Gateway."""
    model = settings.project_owner_agent.selected_model
    adapter: QwenResponsesAdapter | OpenAIResponsesAdapter
    if model.adapter == "qwen":
        adapter = QwenResponsesAdapter(
            base_url=model.base_url,
            timeout_seconds=model.timeout_seconds,
            api_key_env=model.api_key_env,
            http_headers=model.http_headers,
            reasoning_effort=model.reasoning_effort,
            service_tier=model.service_tier,
        )
    elif model.adapter == "openai":
        adapter = OpenAIResponsesAdapter(
            base_url=model.base_url,
            timeout_seconds=model.timeout_seconds,
            api_key_env=model.api_key_env,
            http_headers=model.http_headers,
            reasoning_effort=model.reasoning_effort,
            service_tier=model.service_tier,
        )
    else:
        raise AssertionError(f"Validated model adapter is not implemented: {model.adapter}")
    return ModelGateway(adapter=adapter)
