"""Composition invariants for the sealed Feature Runtime Context."""

from collections.abc import Callable
from pathlib import Path

import pytest

from agentplanex.domains.project_runtime_state import ProjectRuntimeState
from agentplanex.infrastructure.sqlite import SQLiteDatabase, initialize_schema
from agentplanex.project_owner_agent.contracts import Action, ToolExecutionResult
from agentplanex.project_owner_agent.models.responses import (
    ResponsesClient,
    ResponsesRequest,
)
from agentplanex.project_owner_agent.tools import (
    NoToolArguments,
    ToolCatalog,
    ToolDefinition,
)
from agentplanex.services.agent_contracts import (
    AgentPromptCatalog,
    resolve_observation_skill,
)
from agentplanex.services.event_bus import EventBus
from agentplanex.services.project_runtime_context._assembly import (
    _ProjectRuntimeContextAssembly,
    prepare_project_runtime_context,
)
from agentplanex.settings import DEFAULT_SETTINGS_PATH, load_settings


class _UnusedResponsesTransport:
    def create(self, _request: ResponsesRequest) -> object:
        raise AssertionError("Composition tests must not call a model gateway")


def _assembly(project_path: Path) -> _ProjectRuntimeContextAssembly:
    database = SQLiteDatabase.for_project(project_path)
    initialize_schema(database)
    settings = load_settings(DEFAULT_SETTINGS_PATH)
    return prepare_project_runtime_context(
        project_path=project_path,
        database=database,
        event_bus=EventBus(),
        settings=settings,
        approval_mode="yolo",
        responses=ResponsesClient(
            model=settings.project_owner_agent.selected_model.name,
            transport=_UnusedResponsesTransport(),
        ),
        observation_skill=resolve_observation_skill(),
        prompts=AgentPromptCatalog(settings.runtime.prompts),
    )


def _executor(
    _state: ProjectRuntimeState,
    _action: Action,
) -> ToolExecutionResult:
    raise AssertionError("Composition tests must not execute a Tool")


def _tools() -> ToolCatalog:
    return ToolCatalog(
        (
            ToolDefinition(
                name="test_tool",
                description="Test-only composition tool.",
                arguments_type=NoToolArguments,
            ),
        )
    )


def test_prepared_context_cannot_run_before_assembly_is_complete(
    initialize_git_project: Callable[[], Path],
) -> None:
    assembly = _assembly(initialize_git_project())

    with (
        pytest.raises(RuntimeError, match="composition is not sealed"),
        assembly.context.operation(),
    ):
        pass


def test_completed_assembly_produces_a_runnable_context(
    initialize_git_project: Callable[[], Path],
) -> None:
    assembly = _assembly(initialize_git_project())

    assembly.complete(tools=_tools(), tool_executor=_executor)

    with assembly.context.operation():
        pass


def test_assembly_can_only_complete_once(
    initialize_git_project: Callable[[], Path],
) -> None:
    assembly = _assembly(initialize_git_project())
    assembly.complete(tools=_tools(), tool_executor=_executor)

    with pytest.raises(RuntimeError, match="assembly is already complete"):
        assembly.complete(tools=_tools(), tool_executor=_executor)
