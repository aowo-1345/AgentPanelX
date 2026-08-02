"""Shared pytest fixtures.

Keep this file small:
- Put only cross-suite fixtures here.
- Put unit/integration/e2e-specific fixtures in that directory's conftest.py.
- Do not hide business behavior or large mock graphs here.
"""

from collections.abc import Callable
from pathlib import Path
from subprocess import run

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def project_root() -> Path:
    """Return the repository root directory."""
    return PROJECT_ROOT


@pytest.fixture
def fixtures_dir(project_root: Path) -> Path:
    """Return the shared test fixtures directory."""
    return project_root / "tests" / "fixtures"


@pytest.fixture
def git_project_factory(tmp_path: Path) -> Callable[[], Path]:
    """Create independent temporary Git Projects with deterministic identity."""

    def create_project() -> Path:
        project_path = tmp_path / "project"
        project_path.mkdir()
        run(["git", "init", "-b", "main"], cwd=project_path, check=True, capture_output=True)
        run(
            ["git", "config", "user.name", "AgentPlaneX Tests"],
            cwd=project_path,
            check=True,
        )
        run(
            ["git", "config", "user.email", "agentplanex-tests@example.invalid"],
            cwd=project_path,
            check=True,
        )
        (project_path / "README.md").write_text("# Test Project\n", encoding="utf-8")
        run(["git", "add", "README.md"], cwd=project_path, check=True)
        run(["git", "commit", "-m", "chore: initialize project"], cwd=project_path, check=True)
        return project_path

    return create_project
