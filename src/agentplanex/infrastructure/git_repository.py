"""Git operations scoped to one project repository."""

import subprocess
from dataclasses import dataclass
from pathlib import Path


class GitRepositoryError(RuntimeError):
    """A project Git operation failed."""


@dataclass(frozen=True, slots=True)
class GitRepository:
    project_path: Path

    def commit_paths(self, paths: tuple[Path, ...], *, message: str) -> str:
        """Commit only the given project-relative paths and return HEAD."""
        relative_paths = tuple(self._relative_path(path) for path in paths)
        self._run("add", "--", *relative_paths)
        self._run("commit", "-m", message, "--", *relative_paths)
        return self._run("rev-parse", "HEAD").stdout.strip()

    def _relative_path(self, path: Path) -> str:
        try:
            return str(path.resolve().relative_to(self.project_path.resolve()))
        except ValueError as error:
            raise ValueError(f"Git path is outside the project: {path}") from error

    def _run(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            ["git", "-C", str(self.project_path), *arguments],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise GitRepositoryError(
                f"git {' '.join(arguments)} failed: {detail}"
            )
        return result
