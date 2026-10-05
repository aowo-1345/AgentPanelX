"""Pilot contracts for atomic resources and explicit composition boundaries."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from time import sleep

import pytest

import tests.support.resources as resource_module
from agentplanex.infrastructure.sqlite import SQLiteDatabase
from agentplanex.settings import DEFAULT_SETTINGS_PATH, load_settings
from tests.support import (
    CliHarness,
    FakeOwnerFactory,
    FakeStageFactory,
    FeatureRuntimeFactory,
    GitProjectBuilder,
    LazyWorkspaceFactory,
    SQLiteResource,
    compose_test_graph,
)
from tests.support import runtime as runtime_support


def test_git_project_builder_removes_partial_project_after_creation_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_path = tmp_path / "broken-project"

    def fail_git(*_arguments: object) -> None:
        raise RuntimeError("git unavailable")

    monkeypatch.setattr(resource_module, "_run_git", fail_git)

    with pytest.raises(RuntimeError, match="git unavailable"):
        GitProjectBuilder.build(project_path)

    assert not project_path.exists()


def test_sqlite_resource_removes_partial_database_after_schema_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_path = tmp_path / "broken-project"

    def fail_schema(database: SQLiteDatabase) -> None:
        database.path.parent.mkdir(parents=True, exist_ok=True)
        for path in (
            database.path,
            Path(f"{database.path}-wal"),
            Path(f"{database.path}-shm"),
        ):
            path.write_bytes(b"partial")
        raise RuntimeError("schema unavailable")

    monkeypatch.setattr(resource_module, "initialize_schema", fail_schema)

    with pytest.raises(RuntimeError, match="schema unavailable"):
        SQLiteResource.create(project_path)

    assert not (project_path / ".agentplanex" / "agentplanex.sqlite3").exists()
    assert not (project_path / ".agentplanex" / "agentplanex.sqlite3-wal").exists()
    assert not (project_path / ".agentplanex" / "agentplanex.sqlite3-shm").exists()
    assert not (project_path / ".agentplanex").exists()


def test_sqlite_resource_does_not_create_a_git_repository(tmp_path: Path) -> None:
    resource = SQLiteResource.create(tmp_path / "sqlite-project")
    try:
        assert resource.path.is_file()
        assert not (resource.root / ".git").exists()
        with resource.database.connection() as connection:
            assert connection.execute("PRAGMA user_version").fetchone()[0] == 17
    finally:
        resource.close()
        resource.close()
    assert resource.closed


def test_git_project_builder_does_not_create_a_sqlite_database(tmp_path: Path) -> None:
    project = GitProjectBuilder.build(tmp_path / "git-project")
    try:
        assert (project.path / ".git").is_dir()
        assert not (project.path / ".agentplanex" / "agentplanex.sqlite3").exists()
        assert project.repository.head_sha()
    finally:
        project.close()


def test_feature_runtime_requires_and_closes_explicit_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = GitProjectBuilder.build(tmp_path / "feature-project")
    database = SQLiteResource.create(project.path)
    owner = FakeOwnerFactory.create()
    stage = FakeStageFactory.create()
    feature = FeatureRuntimeFactory.create(
        project=project,
        database=database,
        owner=owner,
        stage=stage,
        settings=load_settings(DEFAULT_SETTINGS_PATH),
    )

    try:
        state = feature.runtime.initialize()
        assert state.triage_id
        assert owner.requests == []
    finally:
        feature.close()
        feature.close()

    assert feature.closed
    assert database.closed
    assert owner.closed
    assert stage.closed

    failing_project = GitProjectBuilder.build(tmp_path / "close-failure-project")
    failing_database = SQLiteResource.create(failing_project.path)
    failing_owner = FakeOwnerFactory.create()
    failing_stage = FakeStageFactory.create()
    failing_feature = FeatureRuntimeFactory.create(
        project=failing_project,
        database=failing_database,
        owner=failing_owner,
        stage=failing_stage,
        settings=load_settings(DEFAULT_SETTINGS_PATH),
    )
    original_stage_close = type(failing_stage).close

    def fail_stage_close(resource: object) -> None:
        original_stage_close(resource)  # type: ignore[arg-type]
        raise RuntimeError("stage close failed")

    monkeypatch.setattr(type(failing_stage), "close", fail_stage_close)

    with pytest.raises(RuntimeError, match="stage close failed"):
        failing_feature.close()

    assert failing_feature.closed
    assert failing_stage.closed
    assert failing_owner.closed
    assert failing_database.closed
    assert failing_project.closed


def test_feature_runtime_rejects_resources_from_different_projects(tmp_path: Path) -> None:
    project = GitProjectBuilder.build(tmp_path / "feature-project")
    database = SQLiteResource.create(tmp_path / "other-project")
    owner = FakeOwnerFactory.create()
    stage = FakeStageFactory.create()
    try:
        with pytest.raises(ValueError, match="same project"):
            FeatureRuntimeFactory.create(
                project=project,
                database=database,
                owner=owner,
                stage=stage,
                settings=load_settings(DEFAULT_SETTINGS_PATH),
            )

        assert not (project.path / ".agentplanex" / "agentplanex.sqlite3").exists()
        assert owner.closed
        assert stage.closed
    finally:
        project.close()
        database.close()
        owner.close()
        stage.close()


def test_feature_runtime_rejects_a_closed_database_resource(tmp_path: Path) -> None:
    project = GitProjectBuilder.build(tmp_path / "feature-project")
    database = SQLiteResource.create(project.path)
    owner = FakeOwnerFactory.create()
    stage = FakeStageFactory.create()
    database.close()
    try:
        with pytest.raises(RuntimeError, match="closed SQLite resource"):
            FeatureRuntimeFactory.create(
                project=project,
                database=database,
                owner=owner,
                stage=stage,
                settings=load_settings(DEFAULT_SETTINGS_PATH),
            )
    finally:
        project.close()
        database.close()
        owner.close()
        stage.close()


@pytest.mark.parametrize(
    ("resource", "message"),
    (
        ("project", "closed Git resource"),
        ("owner", "closed Owner"),
        ("stage", "closed Stage"),
    ),
)
def test_feature_runtime_rejects_each_closed_composed_resource(
    tmp_path: Path,
    resource: str,
    message: str,
) -> None:
    project = GitProjectBuilder.build(tmp_path / "feature-project")
    database = SQLiteResource.create(project.path)
    owner = FakeOwnerFactory.create()
    stage = FakeStageFactory.create()
    resources = {
        "project": project,
        "owner": owner,
        "stage": stage,
    }
    resources[resource].close()

    try:
        with pytest.raises(RuntimeError, match=message):
            FeatureRuntimeFactory.create(
                project=project,
                database=database,
                owner=owner,
                stage=stage,
                settings=load_settings(DEFAULT_SETTINGS_PATH),
            )
    finally:
        project.close()
        database.close()
        owner.close()
        stage.close()


def test_feature_runtime_rolls_back_support_resources_when_composition_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = GitProjectBuilder.build(tmp_path / "feature-project")
    database = SQLiteResource.create(project.path)
    owner = FakeOwnerFactory.create()
    stage = FakeStageFactory.create()

    def fail_composition(**_kwargs: object) -> object:
        assert not project.closed
        assert not database.closed
        assert not owner.closed
        assert not stage.closed
        with database.database.connection() as connection:
            connection.execute("CREATE TABLE composition_started (id INTEGER)")
        raise RuntimeError("composition failed")

    monkeypatch.setattr(runtime_support, "compose_test_graph", fail_composition)

    with pytest.raises(RuntimeError, match="composition failed"):
        FeatureRuntimeFactory.create(
            project=project,
            database=database,
            owner=owner,
            stage=stage,
            settings=load_settings(DEFAULT_SETTINGS_PATH),
        )

    assert project.closed
    assert database.closed
    assert owner.closed
    assert stage.closed


def test_composition_validates_all_injected_resources_before_git_side_effect(
    tmp_path: Path,
) -> None:
    project = GitProjectBuilder.build(tmp_path / "feature-project")
    other_database = SQLiteResource.create(tmp_path / "other-project")
    owner = FakeOwnerFactory.create()
    exclude_path = project.path / ".git" / "info" / "exclude"
    original = exclude_path.read_text(encoding="utf-8")
    without_runtime = "\n".join(
        line for line in original.splitlines() if line != ".agentplanex/"
    )
    if original.endswith("\n"):
        without_runtime += "\n"
    exclude_path.write_text(without_runtime, encoding="utf-8")

    try:
        with pytest.raises(ValueError, match="SQLite resource must belong"):
            compose_test_graph(
                project_path=project.path,
                settings=load_settings(DEFAULT_SETTINGS_PATH),
                approval_mode="yolo",
                responses_transport=owner,
                stage_executor=None,
                git=project.repository,
                database=SQLiteDatabase.for_project(other_database.root),
            )

        assert exclude_path.read_text(encoding="utf-8") == without_runtime
    finally:
        project.close()
        other_database.close()
        owner.close()


def test_lazy_workspace_does_not_create_features_until_requested() -> None:
    class _Runtime:
        def __init__(self, key: str, close_error: RuntimeError | None = None) -> None:
            self.key = key
            self.close_error = close_error
            self.closed = False

        def close(self) -> None:
            self.closed = True
            if self.close_error is not None:
                raise self.close_error

    created: list[_Runtime] = []

    def build(key: str) -> _Runtime:
        runtime = _Runtime(key)
        created.append(runtime)
        return runtime

    workspace = LazyWorkspaceFactory.make(
        {"feature-a": object(), "feature-b": object()},
        build,
    )
    assert created == []
    first = workspace.for_feature("feature-a")
    assert workspace.for_feature("feature-a") is first
    assert [runtime.key for runtime in created] == ["feature-a"]

    with pytest.raises(KeyError, match="missing"):
        workspace.for_feature("missing")

    second = workspace.for_feature("feature-b")
    second.close_error = RuntimeError("feature close failed")
    with pytest.raises(RuntimeError, match="feature close failed"):
        workspace.close()
    assert all(runtime.closed for runtime in created)
    assert first.closed and second.closed
    workspace.close()
    with pytest.raises(RuntimeError, match="Workspace handle is closed"):
        workspace.for_feature("feature-a")


def test_lazy_workspace_does_not_cache_a_failed_feature_build() -> None:
    attempts = 0

    class _Runtime:
        def close(self) -> None:
            pass

    def build(_key: str) -> _Runtime:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("feature build failed")
        return _Runtime()

    workspace = LazyWorkspaceFactory.make({"feature-a": object()}, build)
    with pytest.raises(RuntimeError, match="feature build failed"):
        workspace.for_feature("feature-a")
    assert workspace.created_features == ()
    assert workspace.for_feature("feature-a") is not None
    assert attempts == 2
    workspace.close()


def test_lazy_workspace_builds_one_runtime_for_same_key_concurrently() -> None:
    started = Event()
    calls = 0

    class _ClosableRuntime:
        def close(self) -> None:
            pass

    created: list[_ClosableRuntime] = []

    def build(_key: str) -> _ClosableRuntime:
        nonlocal calls
        calls += 1
        started.set()
        sleep(0.05)
        runtime = _ClosableRuntime()
        created.append(runtime)
        return runtime

    workspace = LazyWorkspaceFactory.make({"feature-a": object()}, build)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(workspace.for_feature, "feature-a")
        assert started.wait(timeout=2)
        second = executor.submit(workspace.for_feature, "feature-a")
        futures = [first, second]
        results = [future.result(timeout=2) for future in futures]

    assert calls == 1
    assert results[0] is results[1]
    assert created == [results[0]]
    workspace.close()


def test_cli_harness_preserves_entry_result_and_output() -> None:
    def entry(argv: list[str]) -> int:
        print("argv=" + ",".join(argv))
        return 7

    harness = CliHarness(entry)
    result = harness.run("feature", "view")

    assert result.returncode == 7
    assert result.stdout == "argv=feature,view\n"
    assert result.stderr == ""
    harness.close()
    with pytest.raises(RuntimeError, match="CLI harness is closed"):
        harness.run("feature", "view")


@pytest.mark.parametrize(
    ("exit_code", "expected_code", "expected_stderr"),
    ((None, 0, ""), ("failure", 1, "failure\n")),
)
def test_cli_harness_preserves_system_exit_semantics(
    exit_code: object,
    expected_code: int,
    expected_stderr: str,
) -> None:
    def entry(_argv: list[str]) -> None:
        raise SystemExit(exit_code)

    result = CliHarness(entry).run("feature")

    assert result.returncode == expected_code
    assert result.stderr == expected_stderr
