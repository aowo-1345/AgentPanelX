"""Composition root for one complete Feature Runtime command graph."""

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from agentplanex.infrastructure.agent_workspace import AgentWorkspaceStore
from agentplanex.infrastructure.codex import CodexTurnTransport
from agentplanex.infrastructure.git_repository import GitRepository
from agentplanex.infrastructure.sqlite import SQLiteDatabase, initialize_schema
from agentplanex.infrastructure.sqlite.repositories import SQLiteAutoTakeoverRepository
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
from agentplanex.services._hard_gate import HardGateOperation
from agentplanex.services.agent_collaboration import AgentCollaborationService
from agentplanex.services.agent_collaboration._catalog import AgentCatalog
from agentplanex.services.agent_collaboration._operations import A2AOperation
from agentplanex.services.agent_invocation import AgentPromptCatalog
from agentplanex.services.auto_takeover import AutoTakeoverOperation
from agentplanex.services.delivery._milestone_hard_gate import MilestoneHardGate
from agentplanex.services.delivery._service import DeliveryService
from agentplanex.services.delivery._stage_executor import (
    CodexStageExecutor,
    StageExecutor,
    _StageOperation,
)
from agentplanex.services.event_bus import EventBus
from agentplanex.services.external_agent_runtime import ExternalAgentRuntime
from agentplanex.services.external_agent_runtime._definitions import (
    build_agent_definition,
)
from agentplanex.services.planning._plan_hard_gate import PlanHardGate
from agentplanex.services.planning._service import PlanningService
from agentplanex.services.project_runtime import ProjectRuntimeService
from agentplanex.services.project_runtime_context._assembly import (
    prepare_project_runtime_context,
)
from agentplanex.services.project_runtime_context.context import ProjectRuntimeContext
from agentplanex.settings import Settings


@dataclass(frozen=True, slots=True)
class _ProjectCommandGraph:
    service: ProjectRuntimeService
    context: ProjectRuntimeContext
    executions: ProjectExecutions


def compose_external_agent_runtime(
    *,
    project_path: Path,
    settings: Settings,
) -> ExternalAgentRuntime:
    """Build the shared Owner-external Agent boundary for one Feature."""
    project_path = project_path.resolve()
    if not project_path.is_dir():
        raise ValueError(f"Project path is not a directory: {project_path}")
    codex_settings = settings.runtime.codex
    workspaces = AgentWorkspaceStore(
        project_path=project_path,
        response_limit=codex_settings.response_limit,
        artifact_limit=codex_settings.artifact_limit,
    )
    transport = CodexTurnTransport(
        executable=codex_settings.executable,
        model=codex_settings.model,
        timeout_seconds=codex_settings.timeout_seconds,
        response_limit=codex_settings.response_limit,
        network_access=codex_settings.network_access,
    )
    definitions = {
        key: build_agent_definition(key, configured)
        for key, configured in settings.runtime.external_agents.items()
    }
    operations: dict[tuple[str, str], object] = {
        ("planner", "planner_message_v1"): A2AOperation("planner_message_v1", None, workspaces),
        ("planner", "planner_task_v1"): A2AOperation("planner_task_v1", "plan.md", workspaces),
        ("reviewer", "reviewer_message_v1"): A2AOperation("reviewer_message_v1", None, workspaces),
        ("reviewer", "reviewer_task_v1"): A2AOperation("reviewer_task_v1", "review.md", workspaces),
        ("task_distributor", "task_distributor_message_v1"): A2AOperation(
            "task_distributor_message_v1", None, workspaces
        ),
        ("task_distributor", "task_distribution_v1"): A2AOperation(
            "task_distribution_v1", "milestone-plan.md", workspaces
        ),
        ("plan_hard_gate", "plan_hard_gate_v1"): HardGateOperation("plan_hard_gate_v1"),
        ("milestone_hard_gate", "milestone_hard_gate_v1"): HardGateOperation(
            "milestone_hard_gate_v1"
        ),
        ("stage_executor", "stage_execution_v1"): _StageOperation(),
        ("auto_takeover", "auto_takeover_v1"): AutoTakeoverOperation(),
    }
    return ExternalAgentRuntime(
        workspaces=workspaces,
        transport=transport,
        definitions=MappingProxyType(definitions),
        operations=MappingProxyType(
            {key: operation for key, operation in operations.items() if key[0] in definitions}
        ),
    )


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
    mutation_fence_token: str | None = None,
) -> ProjectRuntimeControl:
    """Return privileged Control over the same sealed command graph design."""
    graph = _compose_command_graph(
        project_path=project_path,
        settings=settings,
        approval_mode=approval_mode,
        responses_transport=responses_transport,
        stage_executor=None,
    )
    return ProjectRuntimeControl(
        _service=graph.service,
        _context=graph.context,
        _mutation_fence_token=mutation_fence_token,
    )


def _compose_command_graph(
    *,
    project_path: Path,
    settings: Settings,
    approval_mode: ApprovalMode,
    responses_transport: ResponsesTransport,
    stage_executor: StageExecutor | None,
) -> _ProjectCommandGraph:
    """Build the sole sealed command graph for one adapter instance."""
    project_path = project_path.resolve()
    if not project_path.is_dir():
        raise ValueError(f"Project path is not a directory: {project_path}")
    git = GitRepository(project_path)
    git.ensure_runtime_excluded()
    database = SQLiteDatabase.for_project(project_path)
    initialize_schema(database)
    takeover_runs = SQLiteAutoTakeoverRepository()
    event_bus = EventBus((SQLiteTimelineRecorder(database),))
    runtime_settings = settings.runtime
    catalog = AgentCatalog(runtime_settings)
    external_agents = compose_external_agent_runtime(
        project_path=project_path,
        settings=settings,
    )
    prompts = AgentPromptCatalog(runtime_settings.prompts)
    collaboration = AgentCollaborationService(
        catalog=catalog,
        runtime=external_agents,
    )
    model_settings = settings.project_owner_agent.selected_model
    assembly = prepare_project_runtime_context(
        project_path=project_path,
        database=database,
        event_bus=event_bus,
        settings=settings,
        approval_mode=approval_mode,
        responses=ResponsesClient(
            model=model_settings.name,
            transport=responses_transport,
        ),
        prompts=prompts,
        mutation_fence_guard=takeover_runs.require_mutation_fence,
    )
    context = assembly.context
    plan_gate = PlanHardGate(
        runtime=external_agents,
    )
    milestone_gate = MilestoneHardGate(
        runtime=external_agents,
    )
    planning = PlanningService(
        project_path=project_path,
        context=context,
        git=git,
        event_bus=event_bus,
        review_plan=plan_gate.review,
    )
    delivery = DeliveryService(
        project_path=project_path,
        context=context,
        git=git,
        stage_executor=(
            stage_executor
            if stage_executor is not None
            else CodexStageExecutor(
                runtime=external_agents,
            )
        ),
        event_bus=event_bus,
        review_milestones=milestone_gate.review,
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
    assembly.complete(
        tools=executions.tools,
        tool_executor=executions.execute,
    )
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
