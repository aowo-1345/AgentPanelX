"""Atomic test resources with explicit ownership and idempotent cleanup."""

from __future__ import annotations

import shutil
import subprocess
from collections import deque
from collections.abc import Callable, Iterable
from contextlib import ExitStack, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentplanex.infrastructure.git_repository import GitRepository
from agentplanex.infrastructure.sqlite import SQLiteDatabase, initialize_schema
from agentplanex.project_owner_agent.models.responses import ResponsesRequest


@dataclass(slots=True)
class GitProject:
    """A Git-only project below pytest's temporary root."""

    path: Path
    repository: GitRepository
    _closed: bool = field(default=False, init=False, repr=False)

    @property
    def project_path(self) -> Path:
        return self.path

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        """Git has no persistent process to close for this resource."""
        self._closed = True


class GitProjectBuilder:
    """Create a committed Git project without creating SQLite state."""

    @staticmethod
    def build(root: Path) -> GitProject:
        project_path = root.resolve()
        project_path.mkdir(parents=True, exist_ok=False)
        with ExitStack() as cleanup:
            cleanup.callback(shutil.rmtree, project_path, ignore_errors=True)
            (project_path / "index.html").write_text("Hello World\n", encoding="utf-8")
            _run_git(project_path, "init", "--initial-branch=main", str(project_path))
            _run_git(project_path, "config", "user.name", "AgentPlaneX Tests")
            _run_git(project_path, "config", "user.email", "tests@agentplanex.local")
            _run_git(project_path, "add", "index.html")
            _run_git(project_path, "commit", "-m", "Initial commit")
            repository = GitRepository(project_path)
            repository.ensure_runtime_excluded()
            project = GitProject(project_path, repository)
            cleanup.pop_all()
            return project


@dataclass(slots=True)
class SQLiteResource:
    """One project-local SQLite schema, independent from Git creation."""

    root: Path
    database: SQLiteDatabase
    _closed: bool = field(default=False, init=False, repr=False)

    @classmethod
    def create(cls, root: Path) -> SQLiteResource:
        project_root = root.resolve()
        project_root.mkdir(parents=True, exist_ok=True)
        database = SQLiteDatabase.for_project(project_root)
        database_directory = database.path.parent
        directory_existed = database_directory.exists()
        database_files = (
            database.path,
            Path(f"{database.path}-wal"),
            Path(f"{database.path}-shm"),
        )
        files_existed = {path: path.exists() for path in database_files}
        try:
            initialize_schema(database)
        except BaseException:
            for path in database_files:
                if not files_existed[path]:
                    path.unlink(missing_ok=True)
            if not directory_existed:
                with suppress(OSError):
                    database_directory.rmdir()
            raise
        return cls(project_root, database)

    @property
    def path(self) -> Path:
        return self.database.path

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        self._closed = True

    def __enter__(self) -> SQLiteResource:
        if self._closed:
            raise RuntimeError("SQLite resource is closed")
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class UnusedResponsesTransport:
    """A transport that makes accidental model calls fail loudly in offline tests."""

    def create(self, _request: ResponsesRequest) -> object:
        raise AssertionError("conversation event test must not call the model")

    def close(self) -> None:
        pass


@dataclass(frozen=True, slots=True)
class OwnerScript:
    responses: tuple[object, ...] = ()
    handler: Callable[[ResponsesRequest], object] | None = None

    @classmethod
    def from_responses(cls, responses: Iterable[object]) -> OwnerScript:
        return cls(tuple(responses))


@dataclass(slots=True)
class FakeOwner:
    """A no-network ResponsesTransport with observable calls."""

    script: OwnerScript = field(default_factory=OwnerScript)
    requests: list[ResponsesRequest] = field(default_factory=list)
    _responses: deque[object] = field(init=False, repr=False)
    _closed: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        self._responses = deque(self.script.responses)

    @property
    def closed(self) -> bool:
        return self._closed

    def create(self, request: ResponsesRequest) -> object:
        if self._closed:
            raise RuntimeError("Fake Owner is closed")
        self.requests.append(request)
        if self.script.handler is not None:
            return self.script.handler(request)
        if self._responses:
            return self._responses.popleft()
        raise AssertionError("Pilot Fake Owner received an unexpected model request")

    def close(self) -> None:
        self._closed = True


class FakeOwnerFactory:
    @staticmethod
    def create(script: OwnerScript | None = None) -> FakeOwner:
        return FakeOwner(script or OwnerScript())


@dataclass(frozen=True, slots=True)
class StageScript:
    handler: Callable[[Any], object] | None = None


@dataclass(slots=True)
class FakeStageExecutor:
    """A StageExecutor-shaped fake that never starts a subprocess."""

    script: StageScript = field(default_factory=StageScript)
    requests: list[Any] = field(default_factory=list)
    _closed: bool = field(default=False, init=False, repr=False)

    @property
    def closed(self) -> bool:
        return self._closed

    def execute(self, request: Any) -> None:
        if self._closed:
            raise RuntimeError("Fake Stage executor is closed")
        self.requests.append(request)
        if self.script.handler is not None:
            self.script.handler(request)

    def close(self) -> None:
        self._closed = True


class FakeStageFactory:
    @staticmethod
    def create(script: StageScript | None = None) -> FakeStageExecutor:
        return FakeStageExecutor(script or StageScript())


def _run_git(project_path: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(project_path), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
