"""Composition invariants for the sealed Feature Runtime Context."""

from collections.abc import Callable
from pathlib import Path

import pytest

from agentplanex.domains import Action, ProjectRuntimeState, ToolExecutionResult
from agentplanex.infrastructure.sqlite import SQLiteDatabase, initialize_schema
from agentplanex.services.event_bus import EventBus
from agentplanex.services.project_runtime_context import ProjectRuntimeContext
from agentplanex.services.project_runtime_context._owner import _OwnerRuntime


def _context(project_path: Path) -> ProjectRuntimeContext:
    database = SQLiteDatabase.for_project(project_path)
    initialize_schema(database)
    return ProjectRuntimeContext(
        project_path=project_path,
        database=database,
        event_bus=EventBus(),
    )


def _executor(
    _state: ProjectRuntimeState,
    _action: Action,
) -> ToolExecutionResult:
    raise AssertionError("Binding tests must not execute a Tool")


def _owner() -> _OwnerRuntime:
    return object.__new__(_OwnerRuntime)


def test_context_cannot_seal_with_a_missing_composition_dependency(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    missing_owner = _context(project_path)
    missing_owner._bind_tool_executor(_executor)
    with pytest.raises(RuntimeError, match="composition is incomplete"):
        missing_owner._seal()

    missing_executor = _context(project_path)
    missing_executor._bind_owner_runtime(_owner())
    with pytest.raises(RuntimeError, match="composition is incomplete"):
        missing_executor._seal()


def test_context_rejects_duplicate_and_post_seal_dependency_binding(
    initialize_git_project: Callable[[], Path],
) -> None:
    context = _context(initialize_git_project())
    context._bind_tool_executor(_executor)
    with pytest.raises(RuntimeError, match="already bound"):
        context._bind_tool_executor(_executor)

    context._bind_owner_runtime(_owner())
    context._seal()

    with pytest.raises(RuntimeError, match="already bound"):
        context._bind_tool_executor(_executor)
    with pytest.raises(RuntimeError, match="already bound"):
        context._bind_owner_runtime(_owner())
