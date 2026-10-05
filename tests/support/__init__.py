"""Explicit, composable resources for the bounded test-architecture pilot."""

from tests.support.harness import CliHarness, ProcessResult, WebHarness
from tests.support.resources import (
    FakeOwner,
    FakeOwnerFactory,
    FakeStageExecutor,
    FakeStageFactory,
    GitProject,
    GitProjectBuilder,
    OwnerScript,
    SQLiteResource,
    StageScript,
    UnusedResponsesTransport,
)
from tests.support.runtime import (
    FeatureRuntime,
    FeatureRuntimeFactory,
    LazyWorkspaceFactory,
    TestRuntimeGraph,
    WorkspaceHandle,
    compose_test_graph,
)

__all__ = [
    "CliHarness",
    "FakeOwner",
    "FakeOwnerFactory",
    "FakeStageExecutor",
    "FakeStageFactory",
    "FeatureRuntime",
    "FeatureRuntimeFactory",
    "GitProject",
    "GitProjectBuilder",
    "LazyWorkspaceFactory",
    "OwnerScript",
    "ProcessResult",
    "SQLiteResource",
    "StageScript",
    "TestRuntimeGraph",
    "UnusedResponsesTransport",
    "WebHarness",
    "WorkspaceHandle",
    "compose_test_graph",
]
