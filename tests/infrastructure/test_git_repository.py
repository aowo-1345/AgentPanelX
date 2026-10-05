"""Behavior checks for project-scoped Git operations."""

from collections.abc import Callable
from pathlib import Path

from agentplanex.infrastructure.git_repository import GitRepository


def test_commit_all_with_ignored_runtime_directory(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    (project_path / "index.html").write_text("Updated\n", encoding="utf-8")

    repository = GitRepository(project_path)
    commit_sha = repository.commit_all(message="Update project")

    assert repository.read_path_at_commit(
        commit_sha,
        project_path / "index.html",
    ) == b"Updated\n"
