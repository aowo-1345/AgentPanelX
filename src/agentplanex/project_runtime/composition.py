"""Composition root for one complete Feature Runtime object graph."""

from dataclasses import dataclass
from pathlib import Path

from agentplanex.infrastructure.git_repository import GitRepository
from agentplanex.infrastructure.sqlite import SQLiteDatabase, initialize_schema
from agentplanex.infrastructure.sqlite.repositories import (
    SQLiteOwnerActivationRepository,
)
from agentplanex.infrastructure.sqlite.timeline import SQLiteTimelineRecorder
from agentplanex.project_owner_agent.approval import ApprovalMode
from agentplanex.project_owner_agent.models.responses import (
    ResponsesClient,
    ResponsesTransport,
)
from agentplanex.project_runtime.executions import create_project_executions
from agentplanex.services import (
    AgentCollaborationService,
    DeliveryService,
    EventBus,
    PlanningService,
    ProjectControlQuery,
    ProjectRuntimeService,
    RuntimeContextService,
)
from agentplanex.services.agent_contracts import resolve_observation_skill
from agentplanex.services.delivery_runner import DeliveryRunner
from agentplanex.services.owner_activation import OwnerActivationDriver
from agentplanex.services.plan_hard_gate import CodexPlanHardGate
from agentplanex.services.project_runtime_context import ProjectRuntimeContext
from agentplanex.services.project_runtime_context._owner import _OwnerRuntime
from agentplanex.services.project_workspace import ProjectWorkspaceQuery
from agentplanex.services.stage_executor import CodexStageExecutor, StageExecutor
from agentplanex.settings import Settings


@dataclass(frozen=True, slots=True)
class _ProjectRuntimeComponents:
    service: ProjectRuntimeService
    context: ProjectRuntimeContext
    workspace_query: ProjectWorkspaceQuery
    git: GitRepository


def compose_project_runtime(
    *,
    project_path: Path,
    settings: Settings,
    approval_mode: ApprovalMode,
    responses_transport: ResponsesTransport,
    stage_executor: StageExecutor | None,
) -> _ProjectRuntimeComponents:
    """Build and seal all command-side collaborators for one worktree."""
    database = SQLiteDatabase.for_project(project_path)
    initialize_schema(database)
    event_bus = EventBus((SQLiteTimelineRecorder(database),))
    context = ProjectRuntimeContext(
        project_path=project_path,
        database=database,
        event_bus=event_bus,
    )

    observation_skill = resolve_observation_skill()
    collaboration = AgentCollaborationService.from_settings(
        project_path,
        settings.runtime,
        observation_skill=observation_skill,
    )
    hard_gate = CodexPlanHardGate(collaboration)
    runtime_contexts = RuntimeContextService(database, event_bus)
    activations = SQLiteOwnerActivationRepository()
    planning = PlanningService(
        project_path=project_path,
        database=database,
        event_bus=event_bus,
        runtime_contexts=runtime_contexts,
        activations=activations,
        review_plan=hard_gate.review,
    )
    git = GitRepository(project_path)
    delivery = DeliveryService(
        project_path=project_path,
        database=database,
        event_bus=event_bus,
        runtime_contexts=runtime_contexts,
        git=git,
        review_milestones=hard_gate.review_milestones,
    )
    delivery_runner = DeliveryRunner(
        delivery=delivery,
        executor=(
            stage_executor
            if stage_executor is not None
            else CodexStageExecutor(
                project_path,
                collaboration.transport,
                collaboration.observation_skill,
                collaboration.prompts,
            )
        ),
        git=git,
    )
    executions = create_project_executions(
        project_path,
        settings.runtime,
        planning,
        delivery,
        collaboration,
        event_bus,
    )
    model_settings = settings.project_owner_agent.selected_model
    owner = _OwnerRuntime(
        database=database,
        settings=settings,
        approval_mode=approval_mode,
        tools=executions.tools,
        tool_executor=executions.execute,
        event_bus=event_bus,
        responses=ResponsesClient(
            model=model_settings.name,
            transport=responses_transport,
        ),
        observation_skill=collaboration.observation_skill,
        prompts=collaboration.prompts,
        load_state=context._reload_state,
    )
    context._bind_owner_runtime(owner)
    context._seal()
    driver = OwnerActivationDriver(
        database=database,
        run_owner=context.run_owner_activation,
        activations=activations,
        event_bus=event_bus,
    )
    service = ProjectRuntimeService(
        planning=planning,
        delivery=delivery,
        delivery_runner=delivery_runner,
        controls=ProjectControlQuery(database=database, git=git),
        event_bus=event_bus,
        context=context,
        activations=activations,
        driver=driver,
    )
    return _ProjectRuntimeComponents(
        service=service,
        context=context,
        workspace_query=ProjectWorkspaceQuery(database=database, git=git),
        git=git,
    )
