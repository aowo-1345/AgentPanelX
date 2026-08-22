"""WorkspaceService-directed scheduling across managed Feature Runtimes."""

import sqlite3
from pathlib import Path
from threading import Event
from time import monotonic, sleep
from typing import cast

import pytest
from fastapi.testclient import TestClient
from loguru import logger

from agentplanex.domains.project_runtime_state import ProjectRuntimeState
from agentplanex.domains.workspace import (
    FeatureAction,
    FeatureBinding,
    ManagedProject,
)
from agentplanex.infrastructure.workspace_git import WorkspaceGit
from agentplanex.infrastructure.workspace_registry import WorkspaceRegistry
from agentplanex.project_runtime import ProjectRuntime
from agentplanex.project_runtime.errors import FeatureBusyError
from agentplanex.services.project_runtime_context.models import (
    OwnerActivation,
    ProjectOwnerTaskType,
)
from agentplanex.services.web import ProjectWorkspaceView
from agentplanex.services.workspace.dispatcher import WorkspaceDispatcher
from agentplanex.services.workspace.errors import WorkspaceCapacityExhaustedError
from agentplanex.services.workspace.queries import (
    FeatureWorkspaceView,
    WorkspaceQueries,
)
from agentplanex.services.workspace.service import WorkspaceService
from agentplanex.settings import DEFAULT_SETTINGS_PATH, load_settings
from agentplanex.web import app as web_app


class _ControlledRuntime:
    def __init__(self, triage_id: str) -> None:
        self.triage_id = triage_id
        self.drive_started = Event()
        self.drive_finished = Event()
        self.release_drive = Event()
        self.submitted_messages: list[str] = []
        self.raise_during_drive = False
        self.trace: list[str] = []
        self.interrupted_work = False
        self.interruption_checks = 0

    def submit_message(self, content: str) -> OwnerActivation:
        self.trace.append("persist:message")
        self.submitted_messages.append(content)
        return OwnerActivation(
            activation_id=f"activation-{self.triage_id}",
            triage_id=self.triage_id,
            task_type=ProjectOwnerTaskType.USER_INPUT,
            message_id=f"message-{self.triage_id}",
        )

    def state(self) -> ProjectRuntimeState:
        return ProjectRuntimeState(triage_id=self.triage_id)

    def drive_until_waiting(self) -> ProjectRuntimeState:
        self.trace.append("drive")
        self.drive_started.set()
        try:
            if not self.release_drive.wait(timeout=5):
                raise TimeoutError(f"Test did not release {self.triage_id}")
            if self.raise_during_drive:
                raise RuntimeError(f"controlled failure for {self.triage_id}")
            return ProjectRuntimeState(triage_id=self.triage_id, status="TODO")
        finally:
            self.drive_finished.set()

    def fail_interrupted_work(self) -> bool:
        self.interruption_checks += 1
        return self.interrupted_work

    def begin_feature(self) -> ProjectRuntimeState:
        self.trace.append("persist:begin")
        return ProjectRuntimeState(triage_id=self.triage_id, status="TODO")

    def approve_plan(self) -> None:
        self.trace.append("persist:approve")

    def reject_plan(self, _feedback: str) -> None:
        self.trace.append("persist:reject")

    def start_first_run(self) -> None:
        self.trace.append("persist:start")


class _ControlledQueries:
    def __init__(self, registry: WorkspaceRegistry) -> None:
        self.registry = registry
        self.failure: Exception | None = None

    def feature_workspace(
        self,
        *,
        project_id: str,
        triage_id: str,
    ) -> FeatureWorkspaceView:
        if self.failure is not None:
            raise self.failure
        binding = self.registry.get_feature(project_id, triage_id)
        return FeatureWorkspaceView(
            project=self.registry.get_project(project_id),
            binding=binding,
            runtime_view=ProjectWorkspaceView(
                state=ProjectRuntimeState(triage_id=triage_id, status="TODO"),
                owner_activation=None,
                activation_has_reply=False,
                runtime_error=None,
                snapshot=None,
                milestones_error=None,
                timeline=(),
                timeline_error=None,
                conversation=(),
                conversation_error=None,
                plan_documents=(),
                plan_error=None,
                git_branch="main",
                git_head="head",
                git_error=None,
                available_actions=(),
            ),
        )


def _workspace(
    tmp_path: Path,
    *,
    max_parallel_features: int,
) -> tuple[WorkspaceService, dict[Path, _ControlledRuntime]]:
    registry = WorkspaceRegistry.at(tmp_path / "registry.sqlite3")
    registry.initialize()
    project = ManagedProject(
        project_id="project-1",
        name="Project",
        repository_path=tmp_path / "repository",
        git_common_dir=tmp_path / "repository" / ".git",
        main_branch="main",
    )
    registry.insert_project(project)
    runtimes: dict[Path, _ControlledRuntime] = {}
    for triage_id in ("feature-a", "feature-b"):
        worktree_path = tmp_path / triage_id
        binding = FeatureBinding(
            triage_id=triage_id,
            project_id=project.project_id,
            name=triage_id,
            worktree_path=worktree_path,
        )
        registry.insert_feature(binding)
        runtimes[worktree_path] = _ControlledRuntime(triage_id)

    def runtime_factory(path: Path) -> ProjectRuntime:
        return cast(ProjectRuntime, runtimes[path])

    git = WorkspaceGit()
    service = WorkspaceService(
        data_home=tmp_path,
        registry=registry,
        git=git,
        queries=cast(WorkspaceQueries, _ControlledQueries(registry)),
        dispatcher=WorkspaceDispatcher(max_parallel_features=max_parallel_features),
        runtime_factory=runtime_factory,
    )
    return service, runtimes


def test_messages_for_different_features_run_in_parallel(tmp_path: Path) -> None:
    workspace, runtimes = _workspace(tmp_path, max_parallel_features=2)
    try:
        first = workspace.submit_feature_message(
            project_id="project-1",
            triage_id="feature-a",
            content="Run feature A",
        )
        assert first.triage_id == "feature-a"
        assert runtimes[tmp_path / "feature-a"].drive_started.wait(timeout=1)

        second = workspace.submit_feature_message(
            project_id="project-1",
            triage_id="feature-b",
            content="Run feature B",
        )
        assert second.triage_id == "feature-b"
        assert runtimes[tmp_path / "feature-b"].drive_started.wait(timeout=1)
    finally:
        for runtime in runtimes.values():
            runtime.release_drive.set()
        workspace.close()


def test_busy_feature_is_rejected_before_message_is_persisted(tmp_path: Path) -> None:
    workspace, runtimes = _workspace(tmp_path, max_parallel_features=2)
    first_runtime = runtimes[tmp_path / "feature-a"]
    try:
        workspace.submit_feature_message(
            project_id="project-1",
            triage_id="feature-a",
            content="First accepted message",
        )
        assert first_runtime.drive_started.wait(timeout=1)

        try:
            workspace.submit_feature_message(
                project_id="project-1",
                triage_id="feature-a",
                content="Must be rejected",
            )
        except FeatureBusyError as error:
            assert error.code == "FEATURE_BUSY"
        else:
            raise AssertionError("Busy Feature request was accepted")

        assert first_runtime.submitted_messages == ["First accepted message"]
    finally:
        first_runtime.release_drive.set()
        workspace.close()


def test_capacity_is_rejected_before_other_feature_message_is_persisted(
    tmp_path: Path,
) -> None:
    workspace, runtimes = _workspace(tmp_path, max_parallel_features=1)
    first_runtime = runtimes[tmp_path / "feature-a"]
    second_runtime = runtimes[tmp_path / "feature-b"]
    try:
        workspace.submit_feature_message(
            project_id="project-1",
            triage_id="feature-a",
            content="Occupy the only slot",
        )
        assert first_runtime.drive_started.wait(timeout=1)

        try:
            workspace.submit_feature_message(
                project_id="project-1",
                triage_id="feature-b",
                content="Must be rejected",
            )
        except WorkspaceCapacityExhaustedError as error:
            assert error.code == "WORKSPACE_CAPACITY_EXHAUSTED"
        else:
            raise AssertionError("Capacity-exhausted request was accepted")

        assert second_runtime.submitted_messages == []
    finally:
        first_runtime.release_drive.set()
        workspace.close()


def test_execution_slot_is_released_and_failure_is_logged(
    tmp_path: Path,
) -> None:
    messages: list[str] = []
    sink_id = logger.add(messages.append, format="{message}")
    workspace, runtimes = _workspace(tmp_path, max_parallel_features=1)
    first_runtime = runtimes[tmp_path / "feature-a"]
    second_runtime = runtimes[tmp_path / "feature-b"]
    first_runtime.raise_during_drive = True
    try:
        workspace.submit_feature_message(
            project_id="project-1",
            triage_id="feature-a",
            content="Fail after acceptance",
        )
        assert first_runtime.drive_started.wait(timeout=1)
        first_runtime.release_drive.set()
        assert first_runtime.drive_finished.wait(timeout=1)

        deadline = monotonic() + 1
        while True:
            try:
                accepted = workspace.submit_feature_message(
                    project_id="project-1",
                    triage_id="feature-b",
                    content="Use the released slot",
                )
                break
            except WorkspaceCapacityExhaustedError:
                if monotonic() >= deadline:
                    raise
                sleep(0.01)

        assert accepted.triage_id == "feature-b"
        assert second_runtime.drive_started.wait(timeout=1)
    finally:
        second_runtime.release_drive.set()
        workspace.close()
        logger.remove(sink_id)
    assert any("Feature Runtime drive failed for feature-a" in message for message in messages)


def test_http_rejects_busy_and_capacity_before_persistence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, runtimes = _workspace(tmp_path, max_parallel_features=1)
    monkeypatch.setattr(web_app, "create_workspace", lambda _settings: workspace)
    first_runtime = runtimes[tmp_path / "feature-a"]
    second_runtime = runtimes[tmp_path / "feature-b"]

    with TestClient(web_app.create_app(load_settings(DEFAULT_SETTINGS_PATH))) as client:
        accepted = client.post(
            "/api/projects/project-1/features/feature-a/messages",
            json={"content": "Occupy the Workspace"},
        )
        assert accepted.status_code == 202
        assert first_runtime.drive_started.wait(timeout=1)

        busy = client.post(
            "/api/projects/project-1/features/feature-a/messages",
            json={"content": "Must not persist"},
        )
        assert busy.status_code == 409
        assert busy.json()["code"] == "FEATURE_BUSY"

        exhausted = client.post(
            "/api/projects/project-1/features/feature-b/messages",
            json={"content": "Must not persist either"},
        )
        assert exhausted.status_code == 429
        assert exhausted.json()["code"] == "WORKSPACE_CAPACITY_EXHAUSTED"
        assert first_runtime.submitted_messages == ["Occupy the Workspace"]
        assert second_runtime.submitted_messages == []

        first_runtime.release_drive.set()


def test_start_only_fails_interrupted_work_without_driving(tmp_path: Path) -> None:
    workspace, runtimes = _workspace(tmp_path, max_parallel_features=2)
    runtimes[tmp_path / "feature-a"].interrupted_work = True
    try:
        failed_features = workspace.start()

        assert failed_features == 1
        assert [runtime.interruption_checks for runtime in runtimes.values()] == [1, 1]
        assert all(runtime.trace == [] for runtime in runtimes.values())
    finally:
        workspace.close()


@pytest.mark.parametrize(
    ("action", "expected_persist"),
    [
        (FeatureAction.APPROVE_PLAN, "persist:approve"),
        (FeatureAction.REJECT_PLAN, "persist:reject"),
        (FeatureAction.START_DELIVERY, "persist:start"),
    ],
)
def test_automatic_actions_persist_before_background_drive(
    tmp_path: Path,
    action: FeatureAction,
    expected_persist: str,
) -> None:
    workspace, runtimes = _workspace(tmp_path, max_parallel_features=1)
    runtime = runtimes[tmp_path / "feature-a"]
    runtime.release_drive.set()
    try:
        result = workspace.perform_feature_action(
            project_id="project-1",
            triage_id="feature-a",
            action=action,
            feedback="Revise the plan" if action is FeatureAction.REJECT_PLAN else "",
        )

        assert result.binding.triage_id == "feature-a"
        assert runtime.drive_finished.wait(timeout=1)
        assert runtime.trace == [expected_persist, "drive"]
    finally:
        workspace.close()


def test_action_projection_failure_does_not_strand_persisted_work(
    tmp_path: Path,
) -> None:
    workspace, runtimes = _workspace(tmp_path, max_parallel_features=1)
    runtime = runtimes[tmp_path / "feature-a"]
    runtime.release_drive.set()
    queries = cast(_ControlledQueries, workspace.queries)
    queries.failure = LookupError("controlled projection failure")
    try:
        with pytest.raises(LookupError, match="controlled projection failure"):
            workspace.perform_feature_action(
                project_id="project-1",
                triage_id="feature-a",
                action=FeatureAction.APPROVE_PLAN,
            )

        assert runtime.drive_finished.wait(timeout=1)
        assert runtime.trace == ["persist:approve", "drive"]
    finally:
        workspace.close()


def test_workspace_query_does_not_create_a_missing_runtime_database(
    tmp_path: Path,
) -> None:
    workspace, _runtimes = _workspace(tmp_path, max_parallel_features=1)
    binding = workspace.registry.get_feature("project-1", "feature-a")
    database_directory = binding.worktree_path / ".agentplanex"
    queries = WorkspaceQueries(registry=workspace.registry, git=workspace.git)
    try:
        with pytest.raises(sqlite3.Error):
            queries.feature_workspace(
                project_id="project-1",
                triage_id="feature-a",
            )

        assert not database_directory.exists()
    finally:
        workspace.close()


def test_begin_is_synchronous_and_does_not_consume_automatic_execution(
    tmp_path: Path,
) -> None:
    workspace, runtimes = _workspace(tmp_path, max_parallel_features=1)
    runtime = runtimes[tmp_path / "feature-a"]
    try:
        result = workspace.perform_feature_action(
            project_id="project-1",
            triage_id="feature-a",
            action=FeatureAction.BEGIN,
        )

        assert result.binding.triage_id == "feature-a"
        assert runtime.trace == ["persist:begin"]
        assert not runtime.drive_started.is_set()
    finally:
        workspace.close()


def test_begin_ignores_global_automatic_capacity_but_keeps_feature_exclusive(
    tmp_path: Path,
) -> None:
    workspace, runtimes = _workspace(tmp_path, max_parallel_features=1)
    automatic_runtime = runtimes[tmp_path / "feature-a"]
    begin_runtime = runtimes[tmp_path / "feature-b"]
    try:
        workspace.submit_feature_message(
            project_id="project-1",
            triage_id="feature-a",
            content="Occupy the only automatic slot",
        )
        assert automatic_runtime.drive_started.wait(timeout=1)

        result = workspace.perform_feature_action(
            project_id="project-1",
            triage_id="feature-b",
            action=FeatureAction.BEGIN,
        )

        assert result.binding.triage_id == "feature-b"
        assert begin_runtime.trace == ["persist:begin"]
    finally:
        automatic_runtime.release_drive.set()
        workspace.close()


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "approve-plan"},
        {"action": "reject-plan", "feedback": "Revise the plan"},
        {"action": "start-delivery"},
    ],
)
def test_http_returns_200_for_begin_and_202_for_automatic_actions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, str],
) -> None:
    workspace, runtimes = _workspace(tmp_path, max_parallel_features=1)
    runtime = runtimes[tmp_path / "feature-a"]
    runtime.release_drive.set()
    monkeypatch.setattr(web_app, "create_workspace", lambda _settings: workspace)

    with TestClient(web_app.create_app(load_settings(DEFAULT_SETTINGS_PATH))) as client:
        begun = client.post(
            "/api/projects/project-1/features/feature-a/actions",
            json={"action": "begin"},
        )
        assert begun.status_code == 200

        accepted = client.post(
            "/api/projects/project-1/features/feature-a/actions",
            json=payload,
        )
        assert accepted.status_code == 202
        assert runtime.drive_finished.wait(timeout=1)
