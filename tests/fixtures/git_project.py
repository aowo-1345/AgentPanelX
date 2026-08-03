"""Create the minimal observable Git project used by tests."""

import subprocess
from pathlib import Path

from agentplanex.infrastructure.sqlite import SQLiteDatabase, initialize_schema

AGENTPLANEX_GIT_EXCLUDE = ".agentplanex/"


def _run_git(project_path: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(project_path), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )


def initialize_git_project(project_path: Path) -> Path:
    """Create a Git project with committed content and local runtime storage."""
    project_path.mkdir(parents=True, exist_ok=False)
    (project_path / "index.html").write_text("Hello World\n", encoding="utf-8")

    subprocess.run(
        ["git", "init", "--initial-branch=main", str(project_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    _run_git(project_path, "config", "user.name", "AgentPlaneX Tests")
    _run_git(project_path, "config", "user.email", "tests@agentplanex.local")
    _run_git(project_path, "add", "index.html")
    _run_git(project_path, "commit", "-m", "Initial commit")

    initialize_project_database(project_path)

    return project_path


def initialize_project_database(project_path: Path) -> SQLiteDatabase:
    """Initialize project-local SQLite state without dirtying its Git worktree."""
    exclude_path = project_path / ".git" / "info" / "exclude"
    exclude_content = exclude_path.read_text(encoding="utf-8")
    if AGENTPLANEX_GIT_EXCLUDE not in exclude_content.splitlines():
        separator = "" if exclude_content.endswith("\n") else "\n"
        exclude_path.write_text(
            f"{exclude_content}{separator}{AGENTPLANEX_GIT_EXCLUDE}\n",
            encoding="utf-8",
        )

    database = SQLiteDatabase.for_project(project_path)
    initialize_schema(database)
    return database
