"""Critical user-visible Project and Feature workspace behavior."""

import json
import shutil
import sqlite3
import subprocess
import sys
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest
import yaml

from agentplanex.bootstrap import create_workspace
from agentplanex.project_owner_agent.models.jbb import JBBModel
from agentplanex.project_runtime import ProjectRuntime
from agentplanex.settings import DEFAULT_SETTINGS_PATH, load_settings


def _git(project_path: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(project_path), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _initialize_repository(path: Path) -> None:
    path.mkdir(parents=True)
    subprocess.run(
        ["git", "init", "--initial-branch=main", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    _git(path, "config", "user.name", "AgentPlaneX Tests")
    _git(path, "config", "user.email", "tests@agentplanex.local")
    (path / "version.txt").write_text("one\n", encoding="utf-8")
    _git(path, "add", "version.txt")
    _git(path, "commit", "-m", "Initial commit")


def _write_settings(path: Path, data_home: Path, model_base_url: str) -> None:
    raw = load_settings(DEFAULT_SETTINGS_PATH).model_dump(mode="json")
    raw["workspace"] = {"data_home": str(data_home)}
    raw["project_owner_agent"]["model"]["base_url"] = model_base_url
    raw["project_owner_agent"]["model"]["timeout_seconds"] = 0.1
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")


def _prepare_case(
    name: str,
    model_base_url: str = "http://127.0.0.1:1/v1",
) -> tuple[Path, Path]:
    case_path = Path(__file__).resolve().parents[1] / ".agentplanex" / "tests" / name
    shutil.rmtree(case_path, ignore_errors=True)
    repository_path = case_path / "repository"
    _initialize_repository(repository_path)
    config_path = case_path / "settings.yaml"
    _write_settings(config_path, case_path / "data-home", model_base_url)
    return repository_path, config_path


@pytest.fixture
def recording_model_endpoint() -> Iterator[tuple[str, list[str]]]:
    requests: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            requests.append(self.path)
            self.send_response(500)
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def _invoke_installed_cli(
    config_path: Path,
    *arguments: str,
) -> subprocess.CompletedProcess[str]:
    executable = Path(sys.executable).with_name("agentplanex")
    return subprocess.run(
        [str(executable), "--config", str(config_path), *arguments],
        check=False,
        capture_output=True,
        text=True,
    )


def _run_installed_cli(config_path: Path, *arguments: str) -> object:
    result = _invoke_installed_cli(config_path, *arguments)
    assert result.returncode == 0, result.stderr
    assert not result.stderr
    return json.loads(result.stdout)


def _runtime_database_snapshot(worktree_path: Path) -> tuple[str, ...]:
    database_path = worktree_path / ".agentplanex" / "agentplanex.sqlite3"
    connection = sqlite3.connect(f"{database_path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        return tuple(connection.iterdump())
    finally:
        connection.close()


def _database_schema(database_path: Path) -> dict[str, tuple[str, ...]]:
    connection = sqlite3.connect(f"{database_path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        tables = connection.execute(
            """
            SELECT name FROM sqlite_schema
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        ).fetchall()
        return {
            str(table[0]): tuple(
                str(column[1])
                for column in connection.execute(
                    f'PRAGMA table_info("{table[0]}")'
                ).fetchall()
            )
            for table in tables
        }
    finally:
        connection.close()


def test_installed_cli_runs_two_isolated_features_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
    recording_model_endpoint: tuple[str, list[str]],
) -> None:
    model_base_url, model_requests = recording_model_endpoint
    repository_path, config_path = _prepare_case(
        "workspace-critical-path",
        model_base_url,
    )
    data_home = config_path.parent / "data-home"

    project = _run_installed_cli(
        config_path,
        "project",
        "register",
        "--name",
        "Example Project",
        "--repository",
        str(repository_path),
        "--main-branch",
        "main",
    )
    assert isinstance(project, dict)
    project_id = project["project_id"]
    assert project == {
        "project_id": project_id,
        "name": "Example Project",
        "repository_path": str(repository_path.resolve()),
        "main_branch": "main",
    }

    first_main_commit = _git(repository_path, "rev-parse", "main")
    feature_a = _run_installed_cli(
        config_path,
        "feature",
        "create",
        "--project",
        project_id,
        "--name",
        "Feature A",
    )
    (repository_path / "version.txt").write_text("two\n", encoding="utf-8")
    _git(repository_path, "add", "version.txt")
    _git(repository_path, "commit", "-m", "Advance main")
    second_main_commit = _git(repository_path, "rev-parse", "main")
    feature_b = _run_installed_cli(
        config_path,
        "feature",
        "create",
        "--project",
        project_id,
        "--name",
        "Feature B",
    )
    assert isinstance(feature_a, dict)
    assert isinstance(feature_b, dict)
    assert feature_a["triage_id"] != feature_b["triage_id"]
    assert feature_a["branch"] != feature_b["branch"]
    assert _run_installed_cli(
        config_path,
        "feature",
        "list",
        "--project",
        project_id,
    ) == [feature_a, feature_b]
    assert _database_schema(data_home / "registry.sqlite3") == {
        "feature_binding": ("triage_id", "project_id", "name", "worktree_path"),
        "managed_project": (
            "project_id",
            "name",
            "repository_path",
            "git_common_dir",
            "main_branch",
        ),
    }

    first_worktree = Path(feature_a["worktree_path"])
    second_worktree = Path(feature_b["worktree_path"])
    assert first_worktree.is_relative_to(data_home / "projects")
    assert second_worktree.is_relative_to(data_home / "projects")
    assert _git(first_worktree, "rev-parse", "HEAD") == first_main_commit
    assert _git(second_worktree, "rev-parse", "HEAD") == second_main_commit
    assert _git(first_worktree, "branch", "--show-current") == feature_a["branch"]
    assert _git(second_worktree, "branch", "--show-current") == feature_b["branch"]
    source_common_dir = _git(
        repository_path,
        "rev-parse",
        "--path-format=absolute",
        "--git-common-dir",
    )
    assert _git(
        first_worktree,
        "rev-parse",
        "--path-format=absolute",
        "--git-common-dir",
    ) == source_common_dir
    assert _git(
        second_worktree,
        "rev-parse",
        "--path-format=absolute",
        "--git-common-dir",
    ) == source_common_dir
    assert _git(first_worktree, "status", "--porcelain") == ""
    assert _git(second_worktree, "status", "--porcelain") == ""

    initial_board = _run_installed_cli(
        config_path,
        "board",
        "--project",
        project_id,
    )
    assert initial_board == {
        "project_id": project_id,
        "name": "Example Project",
        "features": [
            {**feature_a, "status": "TRIAGE"},
            {**feature_b, "status": "TRIAGE"},
        ],
    }

    begun = _run_installed_cli(
        config_path,
        "feature",
        "begin",
        "--project",
        project_id,
        "--feature",
        feature_a["triage_id"],
    )
    assert begun == {
        "project_id": project_id,
        "triage_id": feature_a["triage_id"],
        "status": "TODO",
    }
    repeated_begin = _invoke_installed_cli(
        config_path,
        "feature",
        "begin",
        "--project",
        project_id,
        "--feature",
        feature_a["triage_id"],
    )
    assert repeated_begin.returncode == 1
    assert "Feature can only begin from TRIAGE" in repeated_begin.stderr
    assert not repeated_begin.stdout
    assert model_requests == []

    runtime_files_before_board = {
        first_worktree: _runtime_database_snapshot(first_worktree),
        second_worktree: _runtime_database_snapshot(second_worktree),
    }
    runtime_databases = (
        first_worktree / ".agentplanex" / "agentplanex.sqlite3",
        second_worktree / ".agentplanex" / "agentplanex.sqlite3",
    )
    for database in runtime_databases:
        database.chmod(0o444)
    try:
        recovered_board = _run_installed_cli(
            config_path,
            "board",
            "--project",
            project_id,
        )
    finally:
        for database in runtime_databases:
            database.chmod(0o644)
    assert recovered_board == {
        "project_id": project_id,
        "name": "Example Project",
        "features": [
            {**feature_a, "status": "TODO"},
            {**feature_b, "status": "TRIAGE"},
        ],
    }
    assert {
        first_worktree: _runtime_database_snapshot(first_worktree),
        second_worktree: _runtime_database_snapshot(second_worktree),
    } == runtime_files_before_board

    settings = load_settings(config_path)
    first_runtime = ProjectRuntime(
        project_path=first_worktree,
        settings=settings,
        approval_mode="yolo",
    )
    second_runtime = ProjectRuntime(
        project_path=second_worktree,
        settings=settings,
        approval_mode="yolo",
    )
    first_view = first_runtime.project_control_view()
    second_view = second_runtime.project_control_view()
    assert first_view.context.triage_id == feature_a["triage_id"]
    assert first_view.context.status == "TODO"
    assert first_view.owner_activation is None
    assert len(first_view.timeline) == 1
    assert first_view.timeline[0].payload["reason"] == "FEATURE_BEGUN"
    assert first_view.snapshot is None
    assert first_view.stage_runs == ()
    assert second_view.context.triage_id == feature_b["triage_id"]
    assert second_view.context.status == "TRIAGE"
    assert second_view.owner_activation is None
    assert second_view.timeline == ()
    first_owner = first_runtime.initialize().project_owner_agent
    second_owner = second_runtime.initialize().project_owner_agent
    assert first_owner is not None
    assert second_owner is not None
    assert first_owner.message_id is None
    assert first_owner.summary_id is None
    assert second_owner.message_id is None
    assert second_owner.summary_id is None

    def unexpected_model_call(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("Submitting a Feature message must not drive the model")

    monkeypatch.setattr(JBBModel, "query", unexpected_model_call)
    activation = create_workspace(settings).submit_feature_message(
        project_id=project_id,
        triage_id=feature_a["triage_id"],
        content="Discuss only Feature A",
    )
    assert activation.triage_id == feature_a["triage_id"]

    first_after_message = first_runtime.project_control_view()
    second_after_message = second_runtime.project_control_view()
    assert first_after_message.context.status == "TODO"
    assert first_after_message.owner_activation == activation
    assert second_after_message.context.status == "TRIAGE"
    assert second_after_message.owner_activation is None
    assert second_runtime.initialize().project_owner_agent == second_owner
    assert _run_installed_cli(
        config_path,
        "board",
        "--project",
        project_id,
    ) == recovered_board
    assert _run_installed_cli(config_path, "project", "list") == [project]


def test_installed_cli_rejects_registering_another_worktree_of_same_repository() -> None:
    repository_path, config_path = _prepare_case("workspace-duplicate-project")
    project = _run_installed_cli(
        config_path,
        "project",
        "register",
        "--name",
        "Original",
        "--repository",
        str(repository_path),
        "--main-branch",
        "main",
    )

    linked_worktree = config_path.parent / "linked-worktree"
    _git(repository_path, "branch", "linked-registration", "main")
    _git(
        repository_path,
        "worktree",
        "add",
        str(linked_worktree),
        "linked-registration",
    )
    duplicate = _invoke_installed_cli(
        config_path,
        "project",
        "register",
        "--name",
        "Duplicate",
        "--repository",
        str(linked_worktree),
        "--main-branch",
        "main",
    )
    assert duplicate.returncode == 1
    assert "already registered as Project" in duplicate.stderr
    assert not duplicate.stdout
    assert _run_installed_cli(config_path, "project", "list") == [project]


def test_installed_cli_rejects_missing_project_main_branch() -> None:
    repository_path, config_path = _prepare_case("workspace-missing-main")

    result = _invoke_installed_cli(
        config_path,
        "project",
        "register",
        "--name",
        "Missing Main",
        "--repository",
        str(repository_path),
        "--main-branch",
        "release",
    )
    assert result.returncode == 1
    assert "refs/heads/release" in result.stderr
    assert not result.stdout
    assert _run_installed_cli(config_path, "project", "list") == []
