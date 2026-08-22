"""Explicit test composition helpers that mirror the production Runtime graph."""

from dataclasses import dataclass
from pathlib import Path

from agentplanex.domains.project_runtime_state import ProjectRuntimeState
from agentplanex.project_owner_agent.approval import ApprovalMode
from agentplanex.project_owner_agent.models.responses import (
    ResponsesRequest,
    ResponsesTransport,
)
from agentplanex.project_runtime.composition import _compose_command_graph
from agentplanex.project_runtime.control import ProjectRuntimeControl
from agentplanex.project_runtime.executions import ProjectExecutions
from agentplanex.project_runtime.runtime import ProjectRuntime
from agentplanex.services.delivery._stage_executor import StageExecutor
from agentplanex.services.event_bus import EventBus
from agentplanex.services.planning import PlanningService
from agentplanex.services.project_runtime_context import ProjectRuntimeContext
from agentplanex.settings import (
    DEFAULT_SETTINGS_PATH,
    RuntimeSettings,
    Settings,
    load_settings,
)


class _UnusedResponsesTransport(ResponsesTransport):
    def create(self, _request: ResponsesRequest) -> object:
        raise AssertionError("Tool contract tests must not call a model gateway")


@dataclass(frozen=True, slots=True)
class ComposedTestExecutions:
    executions: ProjectExecutions
    state: ProjectRuntimeState
    context: ProjectRuntimeContext
    planning: PlanningService
    event_bus: EventBus


@dataclass(frozen=True, slots=True)
class RuntimePair:
    """Test access to the two real adapters over one composed command graph."""

    runtime: ProjectRuntime
    control: ProjectRuntimeControl


def compose_test_runtime(
    *,
    project_path: Path,
    settings: Settings,
    approval_mode: ApprovalMode,
    responses_transport: ResponsesTransport,
    stage_executor: StageExecutor | None = None,
) -> RuntimePair:
    graph = _compose_command_graph(
        project_path=project_path,
        settings=settings,
        approval_mode=approval_mode,
        responses_transport=responses_transport,
        stage_executor=stage_executor,
    )
    return RuntimePair(
        runtime=ProjectRuntime(_service=graph.service),
        control=ProjectRuntimeControl(
            _service=graph.service,
            _context=graph.context,
        ),
    )


def compose_test_executions(
    project_path: Path,
    runtime_settings: RuntimeSettings | None = None,
) -> ComposedTestExecutions:
    settings = load_settings(DEFAULT_SETTINGS_PATH)
    if runtime_settings is not None:
        settings = settings.model_copy(update={"runtime": runtime_settings})
    graph = _compose_command_graph(
        project_path=project_path,
        settings=settings,
        approval_mode="yolo",
        responses_transport=_UnusedResponsesTransport(),
        stage_executor=None,
    )
    state = graph.context.initialize()
    return ComposedTestExecutions(
        executions=graph.executions,
        state=state,
        context=graph.context,
        planning=graph.service.planning,
        event_bus=graph.context.event_bus,
    )
