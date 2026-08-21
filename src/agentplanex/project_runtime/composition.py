"""Composition root for one complete Feature Runtime command graph."""

from dataclasses import dataclass
from pathlib import Path

from agentplanex.infrastructure.git_repository import GitRepository
from agentplanex.infrastructure.sqlite import SQLiteDatabase, initialize_schema
from agentplanex.infrastructure.sqlite.timeline import SQLiteTimelineRecorder
from agentplanex.project_owner_agent.approval import ApprovalMode
from agentplanex.project_owner_agent.models.responses import (
    ResponsesClient,
    ResponsesTransport,
)
from agentplanex.project_runtime.control import ProjectRuntimeControl
from agentplanex.project_runtime.executions import (
    ProjectExecutions,
    create_project_executions,
)
from agentplanex.project_runtime.runtime import ProjectRuntime
from agentplanex.services.agent_collaboration import AgentCollaborationService
from agentplanex.services.agent_contracts import resolve_observation_skill
from agentplanex.services.delivery import DeliveryService
from agentplanex.services.delivery._stage_executor import (
    CodexStageExecutor,
    StageExecutor,
)
from agentplanex.services.event_bus import EventBus
from agentplanex.services.plan_hard_gate import CodexPlanHardGate
from agentplanex.services.planning import PlanningService
from agentplanex.services.project_runtime import ProjectRuntimeService
from agentplanex.services.project_runtime_context import ProjectRuntimeContext
from agentplanex.services.project_runtime_context._owner import _OwnerRuntime
from agentplanex.settings import Settings


@dataclass(frozen=True, slots=True)
class _ProjectCommandGraph:
    service: ProjectRuntimeService
    context: ProjectRuntimeContext
    executions: ProjectExecutions


def compose_project_runtime(
    *,
    project_path: Path,
    settings: Settings,
    approval_mode: ApprovalMode,
    responses_transport: ResponsesTransport,
) -> ProjectRuntime:
    """Return the sealed normal Runtime rather than its internal object graph."""
    graph = _compose_command_graph(
        project_path=project_path,
        settings=settings,
        approval_mode=approval_mode,
        responses_transport=responses_transport,
        stage_executor=None,
    )
    return ProjectRuntime(_service=graph.service)


def compose_project_runtime_control(
    *,
    project_path: Path,
    settings: Settings,
    approval_mode: ApprovalMode,
    responses_transport: ResponsesTransport,
) -> ProjectRuntimeControl:
    """Return privileged Control over the same sealed command graph design."""
    graph = _compose_command_graph(
        project_path=project_path,
        settings=settings,
        approval_mode=approval_mode,
        responses_transport=responses_transport,
        stage_executor=None,
    )
    return ProjectRuntimeControl(_service=graph.service, _context=graph.context)


def _compose_command_graph(
    *,
    project_path: Path,
    settings: Settings,
    approval_mode: ApprovalMode,
    responses_transport: ResponsesTransport,
    stage_executor: StageExecutor | None,
) -> _ProjectCommandGraph:
    """Build, bind, and seal the sole command graph for one adapter instance."""
    project_path = project_path.resolve()
    if not project_path.is_dir():
        raise ValueError(f"Project path is not a directory: {project_path}")
    git = GitRepository(project_path)
    git.ensure_runtime_excluded()
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
    planning = PlanningService(
        project_path=project_path,
        context=context,
        git=git,
        event_bus=event_bus,
        review_plan=hard_gate.review,
    )
    delivery = DeliveryService(
        project_path=project_path,
        context=context,
        git=git,
        stage_executor=(
            stage_executor
            if stage_executor is not None
            else CodexStageExecutor(
                project_path,
                collaboration.transport,
                collaboration.observation_skill,
                collaboration.prompts,
            )
        ),
        event_bus=event_bus,
        review_milestones=hard_gate.review_milestones,
    )
    executions = create_project_executions(
        project_path,
        settings.runtime,
        context=context,
        planning=planning,
        delivery=delivery,
        collaboration=collaboration,
        event_bus=event_bus,
    )
    context._bind_tool_executor(executions.execute)
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
        set_activation_initial_summary=context._set_owner_activation_initial_summary,
    )
    context._bind_owner_runtime(owner)
    context._seal()
    service = ProjectRuntimeService(
        planning=planning,
        delivery=delivery,
        context=context,
    )
    return _ProjectCommandGraph(
        service=service,
        context=context,
        executions=executions,
    )
