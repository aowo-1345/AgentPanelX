"""Explicit test composition helpers that mirror the production Runtime graph."""

from dataclasses import dataclass
from pathlib import Path

from agentplanex.domains import ProjectRuntimeState
from agentplanex.project_owner_agent.models.responses import (
    ResponsesRequest,
    ResponsesTransport,
)
from agentplanex.project_runtime.composition import compose_project_runtime
from agentplanex.project_runtime.executions import ProjectExecutions
from agentplanex.services.event_bus import EventBus
from agentplanex.services.planning import PlanningService
from agentplanex.services.project_runtime_context import ProjectRuntimeContext
from agentplanex.settings import DEFAULT_SETTINGS_PATH, RuntimeSettings, load_settings


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


def compose_test_executions(
    project_path: Path,
    runtime_settings: RuntimeSettings | None = None,
) -> ComposedTestExecutions:
    settings = load_settings(DEFAULT_SETTINGS_PATH)
    if runtime_settings is not None:
        settings = settings.model_copy(update={"runtime": runtime_settings})
    components = compose_project_runtime(
        project_path=project_path,
        settings=settings,
        approval_mode="yolo",
        responses_transport=_UnusedResponsesTransport(),
        stage_executor=None,
    )
    state = components.context.initialize()
    return ComposedTestExecutions(
        executions=components.executions,
        state=state,
        context=components.context,
        planning=components.service.planning,
        event_bus=components.service.event_bus,
    )
