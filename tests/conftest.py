"""Shared pytest fixtures."""

import hashlib
import re
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from tests.fixtures import initialize_git_project as _initialize_git_project

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def initialize_git_project(request: pytest.FixtureRequest) -> Callable[[], Path]:
    """Provide one observable Git project for the current test."""

    node_id = request.node.nodeid
    readable_id = re.sub(r"[^A-Za-z0-9._-]+", "_", node_id).strip("._") or "test"
    digest = hashlib.sha256(node_id.encode("utf-8")).hexdigest()[:10]
    project_path = PROJECT_ROOT / ".agentplanex" / "tests" / f"{readable_id[:100]}-{digest}"
    shutil.rmtree(project_path, ignore_errors=True)

    def create_project() -> Path:
        return _initialize_git_project(project_path)

    return create_project
