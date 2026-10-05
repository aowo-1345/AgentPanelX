"""Explicit Feature Runtime and lazy Workspace composition for pilot tests."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Protocol

from agentplanex.infrastructure.git_repository import GitRepository
from agentplanex.infrastructure.sqlite import SQLiteDatabase
from agentplanex.project_owner_agent.approval import ApprovalMode
from agentplanex.project_owner_agent.models.responses import ResponsesTransport
from agentplanex.project_runtime.composition import _compose_command_graph
from agentplanex.project_runtime.control import ProjectRuntimeControl
from agentplanex.project_runtime.executions import ProjectExecutions
from agentplanex.project_runtime.runtime import ProjectRuntime
from agentplanex.services.delivery._stage_executor import StageExecutor
from agentplanex.services.event_bus import EventBus
from agentplanex.services.planning._service import PlanningService
from agentplanex.services.project_runtime import ProjectRuntimeService
from agentplanex.services.project_runtime_context.context import ProjectRuntimeContext
from agentplanex.services.web.conversation_hub import ConversationHub
from agentplanex.settings import Settings
from tests.support.resources import (
    FakeOwner,
    FakeStageExecutor,
    GitProject,
    SQLiteResource,
)


class ClosableRuntime(Protocol):
    """The minimal lifecycle contract required by a lazy Workspace handle."""

    def close(self) -> None:
        ...


@dataclass(frozen=True, slots=True)
class TestRuntimeGraph:
    """The one support-owned adapter around the production Runtime graph."""

    service: ProjectRuntimeService
    context: ProjectRuntimeContext
    executions: ProjectExecutions
    planning: PlanningService
    event_bus: EventBus


def compose_test_graph(
    *,
    project_path: Path,
    settings: Settings,
    approval_mode: ApprovalMode,
    responses_transport: ResponsesTransport,
    stage_executor: StageExecutor | None = None,
    git: GitRepository | None = None,
    database: SQLiteDatabase | None = None,
    conversation_hub: ConversationHub | None = None,
) -> TestRuntimeGraph:
    """Compose the shared test graph through one traceable support boundary."""
    graph = _compose_command_graph(
        project_path=project_path,
        settings=settings,
        approval_mode=approval_mode,
        responses_transport=responses_transport,
        stage_executor=stage_executor,
        git=git,
        database=database,
        conversation_hub=conversation_hub,
    )
    return TestRuntimeGraph(
        service=graph.service,
        context=graph.context,
        executions=graph.executions,
        planning=graph.service.planning,
        event_bus=graph.context.event_bus,
    )


@dataclass(slots=True)
class FeatureRuntime:
    project: GitProject
    database: SQLiteResource
    owner: FakeOwner
    stage: FakeStageExecutor
    runtime: ProjectRuntime
    control: ProjectRuntimeControl
    conversation_hub: ConversationHub | None = None
    _closed: bool = field(default=False, init=False, repr=False)

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        first_error: BaseException | None = None
        close_operations = []
        if self.conversation_hub is not None:
            close_operations.append(self.conversation_hub.close)
        close_operations.extend(
            (self.stage.close, self.owner.close, self.database.close, self.project.close)
        )
        for close in close_operations:
            try:
                close()
            except BaseException as error:
                if first_error is None:
                    first_error = error
        if first_error is not None:
            raise first_error

    def __enter__(self) -> FeatureRuntime:
        if self._closed:
            raise RuntimeError("Feature Runtime is closed")
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class FeatureRuntimeFactory:
    """Compose one Feature Runtime only when a test requests it explicitly."""

    @staticmethod
    def create(
        *,
        project: GitProject,
        database: SQLiteResource,
        owner: FakeOwner,
        stage: FakeStageExecutor,
        settings: Settings,
        approval_mode: ApprovalMode = "yolo",
        conversation_hub: ConversationHub | None = None,
    ) -> FeatureRuntime:
        project_root = project.path.resolve()
        with ExitStack() as rollback:
            rollback.callback(project.close)
            rollback.callback(database.close)
            rollback.callback(owner.close)
            rollback.callback(stage.close)
            if conversation_hub is not None:
                rollback.callback(conversation_hub.close)

            if project.closed:
                raise RuntimeError("Cannot compose a Feature Runtime from a closed Git resource")
            if not project_root.is_dir():
                raise RuntimeError("Cannot compose a Feature Runtime from a missing Git project")
            if database.closed:
                raise RuntimeError(
                    "Cannot compose a Feature Runtime from a closed SQLite resource"
                )
            if database.root.resolve() != project_root:
                raise ValueError(
                    "Feature Runtime requires Git and SQLite resources for the same project"
                )
            if owner.closed:
                raise RuntimeError("Cannot compose a Feature Runtime from a closed Owner")
            if stage.closed:
                raise RuntimeError("Cannot compose a Feature Runtime from a closed Stage")

            graph = compose_test_graph(
                project_path=project_root,
                settings=settings,
                approval_mode=approval_mode,
                responses_transport=owner,
                stage_executor=stage,
                git=project.repository,
                database=database.database,
                conversation_hub=conversation_hub,
            )
            feature = FeatureRuntime(
                project=project,
                database=database,
                owner=owner,
                stage=stage,
                runtime=ProjectRuntime(_service=graph.service),
                control=ProjectRuntimeControl(
                    _service=graph.service,
                    _context=graph.context,
                ),
                conversation_hub=conversation_hub,
            )
            rollback.pop_all()
            return feature


@dataclass(slots=True)
class WorkspaceHandle[RuntimeT: ClosableRuntime]:
    _feature_keys: frozenset[str]
    _builder: Callable[[str], RuntimeT]
    _runtimes: dict[str, RuntimeT] = field(default_factory=dict)
    _guard: Lock = field(default_factory=Lock, init=False, repr=False)
    _closed: bool = field(default=False, init=False, repr=False)

    @property
    def created_features(self) -> tuple[str, ...]:
        with self._guard:
            return tuple(self._runtimes)

    @property
    def closed(self) -> bool:
        with self._guard:
            return self._closed

    def for_feature(self, feature_key: str) -> RuntimeT:
        with self._guard:
            if self._closed:
                raise RuntimeError("Workspace handle is closed")
            if feature_key not in self._feature_keys:
                raise KeyError(f"Unknown Feature: {feature_key}")
            runtime = self._runtimes.get(feature_key)
            if runtime is None:
                runtime = self._builder(feature_key)
                self._runtimes[feature_key] = runtime
            return runtime

    def close(self) -> None:
        with self._guard:
            if self._closed:
                return
            self._closed = True
            first_error: BaseException | None = None
            for runtime in reversed(tuple(self._runtimes.values())):
                try:
                    runtime.close()
                except BaseException as error:
                    if first_error is None:
                        first_error = error
            if first_error is not None:
                raise first_error

    def __enter__(self) -> WorkspaceHandle[RuntimeT]:
        if self.closed:
            raise RuntimeError("Workspace handle is closed")
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class LazyWorkspaceFactory:
    """Create only registry/binding state until a Feature is requested."""

    @staticmethod
    def make[RuntimeT: ClosableRuntime](
        registry: Mapping[str, object],
        feature_builder: Callable[[str], RuntimeT],
    ) -> WorkspaceHandle[RuntimeT]:
        return WorkspaceHandle(frozenset(registry), feature_builder)
