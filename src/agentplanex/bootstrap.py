"""Application composition for a project-scoped Runtime."""

import os
from pathlib import Path

import yaml

from agentplanex.domains.workspace import FeatureBinding
from agentplanex.infrastructure.agent_workspace import AgentWorkspaceStore
from agentplanex.infrastructure.git_repository import GitRepository
from agentplanex.infrastructure.github_issue import GitHubIssuePublisher
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
    compose_external_agent_runtime,
    compose_project_runtime,
    compose_project_runtime_control,
)
from agentplanex.project_runtime.control import ProjectRuntimeControl
from agentplanex.services.auto_takeover import AutoTakeoverService
from agentplanex.services.project_control import ProjectControlQuery
from agentplanex.services.web import ProjectWorkspaceQuery
from agentplanex.services.web.to_issue import ProposalToIssue
from agentplanex.services.workspace.dispatcher import WorkspaceDispatcher
from agentplanex.services.workspace.queries import WorkspaceQueries
from agentplanex.services.workspace.service import WorkspaceService
from agentplanex.settings import Settings, load_settings

_CLIPROXY_API_KEY_ENV = "CLIPROXY_API_KEY"


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
    mutation_fence_token: str | None = None,
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
        mutation_fence_token=mutation_fence_token,
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
    codex_settings = load_settings().runtime.codex
    return ProjectWorkspaceQuery(
        database=SQLiteDatabase.for_project(resolved),
        git=GitRepository(resolved),
        artifacts=AgentWorkspaceStore(
            project_path=resolved,
            response_limit=codex_settings.response_limit,
            artifact_limit=codex_settings.artifact_limit,
        ),
    )


def create_workspace(
    settings: Settings,
    *,
    settings_path: Path | None = None,
) -> WorkspaceService:
    """Compose the user-level Workspace over real Registry, Git, and Runtimes."""
    configure_logging()
    responses_transport = create_responses_transport(settings)
    registry = WorkspaceRegistry.at(settings.workspace.data_home / "registry.sqlite3")
    registry.initialize()
    git = WorkspaceGit()
    dispatcher = WorkspaceDispatcher(max_parallel_features=settings.workspace.max_parallel_features)

    def runtime_factory(project_path: Path) -> ProjectRuntime:
        return create_project_runtime(
            project_path=project_path,
            approval_mode="yolo",
            settings=settings,
            responses_transport=responses_transport,
        )

    takeover: AutoTakeoverService | None = None
    if settings.runtime.auto_takeover.enabled:
        if settings_path is None:
            raise ValueError("Ultra Mode requires the Runtime settings file path")

        def schedule_drive(binding: FeatureBinding) -> None:
            active_takeover = takeover
            assert active_takeover is not None
            watermark = active_takeover.event_watermark(binding)
            runtime = runtime_factory(binding.worktree_path)
            dispatcher.dispatch(
                binding.triage_id,
                persist=lambda: None,
                drive=runtime.drive_until_waiting,
                after_release=lambda: active_takeover.after_drive_released(
                    binding,
                    after_event_id=watermark,
                ),
            )

        takeover = AutoTakeoverService(
            external_runtime_factory=lambda project_path: compose_external_agent_runtime(
                project_path=project_path,
                settings=settings,
            ),
            schedule_drive=schedule_drive,
            settings_path=settings_path,
            budget_seconds=settings.runtime.auto_takeover.budget_seconds,
            max_parallel_features=settings.workspace.max_parallel_features,
        )
    return WorkspaceService(
        data_home=settings.workspace.data_home,
        registry=registry,
        git=git,
        queries=WorkspaceQueries(
            registry=registry,
            git=git,
            artifact_response_limit=settings.runtime.codex.response_limit,
            artifact_limit=settings.runtime.codex.artifact_limit,
        ),
        dispatcher=dispatcher,
        runtime_factory=runtime_factory,
        proposal_to_issue=ProposalToIssue(
            publisher=GitHubIssuePublisher(data_home=settings.workspace.data_home),
            artifact_response_limit=settings.runtime.codex.response_limit,
            artifact_limit=settings.runtime.codex.artifact_limit,
        ),
        auto_takeover=takeover,
        close_resources=responses_transport.close,
    )


def create_responses_transport(settings: Settings) -> ModelGateway:
    """Bind the selected model Adapter to one application Gateway."""
    model = settings.project_owner_agent.selected_model
    adapter_type: type[QwenResponsesAdapter] | type[OpenAIResponsesAdapter]
    if model.adapter == "qwen":
        adapter_type = QwenResponsesAdapter
    elif model.adapter == "openai":
        adapter_type = OpenAIResponsesAdapter
    else:
        raise AssertionError(f"Validated model adapter is not implemented: {model.adapter}")
    adapter = adapter_type(
        base_url=model.base_url,
        timeout_seconds=model.timeout_seconds,
        max_retries=model.max_retries,
        api_key_env=model.api_key_env,
        fallback_api_key=_local_cliproxy_api_key(settings, model.api_key_env),
        http_headers=model.http_headers,
        reasoning_effort=model.reasoning_effort,
        service_tier=model.service_tier,
    )
    return ModelGateway(adapter=adapter)


def _local_cliproxy_api_key(settings: Settings, api_key_env: str) -> str | None:
    if api_key_env != _CLIPROXY_API_KEY_ENV or os.getenv(api_key_env, "").strip():
        return None
    path = settings.workspace.data_home / "secrets" / "cliproxy" / "config.yaml"
    try:
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(config, dict) or not isinstance(config.get("api-keys"), list):
        return None
    return next(
        (
            key.strip()
            for key in config["api-keys"]
            if isinstance(key, str) and key.strip()
        ),
        None,
    )
