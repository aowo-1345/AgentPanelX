"""Shared pytest fixtures."""

from collections.abc import Callable
from pathlib import Path

import pytest

from tests.fixtures import initialize_git_project as _initialize_git_project


@pytest.fixture
def initialize_git_project(tmp_path: Path) -> Callable[[], Path]:
    """Provide one observable Git project for the current test."""

    project_path = tmp_path / "project"

    def create_project() -> Path:
        return _initialize_git_project(project_path)

    return create_project
