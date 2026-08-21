"""Git operations scoped to one project repository."""

import subprocess
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

_RUNTIME_DIRECTORY = ".agentplanex"
_WORKTREE_DIRECTORY = "delivery-worktrees"


class GitRepositoryError(RuntimeError):
    """A project Git operation failed."""


@dataclass(frozen=True, slots=True)
class GitRepository:
    project_path: Path

    def head_sha(self) -> str:
        """Return the exact commit currently checked out."""
        return self._run("rev-parse", "HEAD").stdout.strip()

    def current_branch(self) -> str:
        """Return the attached branch name, rejecting detached target worktrees."""
        branch = self._run("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        if branch == "HEAD":
            raise GitRepositoryError("Project target worktree is detached")
        return branch

    def assert_clean(self) -> None:
        """Reject project changes outside the Runtime-owned directory."""
        changed = self.changed_paths()
        if changed:
            raise GitRepositoryError(
                "Git worktree has uncommitted changes: " + ", ".join(changed)
            )

    def ensure_runtime_excluded(self) -> None:
        """Keep project-local Runtime data out of the attached Feature branch."""
        result = self._run("rev-parse", "--git-path", "info/exclude")
        exclude_path = Path(result.stdout.strip())
        if not exclude_path.is_absolute():
            exclude_path = self.project_path / exclude_path
        temporary: Path | None = None
        try:
            existing = exclude_path.read_text(encoding="utf-8")
            runtime_pattern = f"{_RUNTIME_DIRECTORY}/"
            if runtime_pattern in existing.splitlines():
                return
            separator = "" if not existing or existing.endswith("\n") else "\n"
            temporary = exclude_path.with_name(
                f".{exclude_path.name}.{uuid4().hex}.tmp"
            )
            temporary.write_text(
                f"{existing}{separator}{runtime_pattern}\n",
                encoding="utf-8",
            )
            temporary.replace(exclude_path)
        except OSError as error:
            raise GitRepositoryError(
                "Cannot update project-local Git exclude"
            ) from error
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def changed_paths(self) -> tuple[str, ...]:
        """Return changed project paths while excluding Runtime-owned data."""
        result = self._run(
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
        )
        entries = result.stdout.split("\0")
        paths: list[str] = []
        index = 0
        while index < len(entries):
            entry = entries[index]
            index += 1
            if not entry:
                continue
            if len(entry) < 4 or entry[2] != " ":
                raise GitRepositoryError("Git returned an invalid status record")
            status = entry[:2]
            path = entry[3:]
            if "R" in status or "C" in status:
                if index >= len(entries) or not entries[index]:
                    raise GitRepositoryError("Git returned an incomplete rename record")
                index += 1
            if path != _RUNTIME_DIRECTORY and not path.startswith(
                f"{_RUNTIME_DIRECTORY}/"
            ):
                paths.append(path)
        return tuple(paths)

    def paths_changed_from_commit(
        self,
        commit_sha: str,
        paths: tuple[Path, ...],
        *,
        target_commit_sha: str | None = None,
    ) -> tuple[str, ...]:
        """Return selected paths differing from a fixed commit or the worktree."""
        relative_paths = tuple(self._relative_path(path) for path in paths)
        arguments = ["diff", "--name-only", commit_sha]
        if target_commit_sha is not None:
            arguments.append(target_commit_sha)
        arguments.extend(("--", *relative_paths))
        return tuple(
            path
            for path in self._run(*arguments).stdout.splitlines()
            if path
        )

    def commit_paths(self, paths: tuple[Path, ...], *, message: str) -> str:
        """Commit only the given project-relative paths and return HEAD."""
        relative_paths = tuple(self._relative_path(path) for path in paths)
        self._run("add", "--", *relative_paths)
        self._run("commit", "-m", message, "--", *relative_paths)
        return self.head_sha()

    def read_path_at_commit(self, commit_sha: str, path: Path) -> bytes:
        """Read one project-relative file exactly as stored by Git."""
        relative_path = self._relative_path(path)
        arguments = ("show", f"{commit_sha}:{relative_path}")
        result = subprocess.run(
            ["git", "-C", str(self.project_path), *arguments],
            check=False,
            capture_output=True,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).decode(
                "utf-8",
                errors="replace",
            ).strip()
            raise GitRepositoryError(
                f"git {' '.join(arguments)} failed: {detail}"
            )
        return result.stdout

    def commit_parent(self, commit_sha: str) -> str:
        """Return the sole first parent of one commit."""
        return self._run("rev-parse", f"{commit_sha}^").stdout.strip()

    def changed_paths_between(
        self,
        base_commit_sha: str,
        target_commit_sha: str,
    ) -> tuple[str, ...]:
        """Return every project path changed between two commits."""
        return tuple(
            path
            for path in self._run(
                "diff",
                "--name-only",
                base_commit_sha,
                target_commit_sha,
                "--",
            ).stdout.splitlines()
            if path
        )

    def prepare_delivery_worktree(self, run_id: str, input_commit_sha: str) -> Path:
        """Create or restore one clean detached worktree at the Stage input commit."""
        path = self.delivery_worktree_path(run_id)
        if path.exists():
            repository = GitRepository(path)
            if repository.head_sha() != input_commit_sha:
                raise GitRepositoryError(
                    "Delivery worktree HEAD does not match Stage input commit"
                )
            repository.assert_clean()
            return path

        path.parent.mkdir(parents=True, exist_ok=True)
        self._run("worktree", "prune")
        self._run("worktree", "add", "--detach", str(path), input_commit_sha)
        return path

    def remove_delivery_worktree(self, run_id: str) -> None:
        """Remove a terminal Run worktree through Git's worktree boundary."""
        path = self.delivery_worktree_path(run_id)
        if path.exists():
            self._run("worktree", "remove", "--force", str(path))
        self._run("worktree", "prune")

    def delivery_worktree_path(self, run_id: str) -> Path:
        """Return the deterministic Runtime-owned path for a Run worktree."""
        self._require_identifier("run_id", run_id)
        return (
            self.project_path.resolve()
            / _RUNTIME_DIRECTORY
            / _WORKTREE_DIRECTORY
            / run_id
        )

    def commit_all(self, *, message: str) -> str:
        """Commit all non-Runtime worktree changes and return the new HEAD."""
        if not self.changed_paths():
            raise GitRepositoryError("Stage produced no Git changes")
        self._run(
            "add",
            "-A",
            "--",
            ".",
            f":(exclude){_RUNTIME_DIRECTORY}",
        )
        staged = self._run_unchecked("diff", "--cached", "--quiet")
        if staged.returncode == 0:
            raise GitRepositoryError("Stage produced no committable Git changes")
        if staged.returncode != 1:
            self._raise_failed(staged, ("diff", "--cached", "--quiet"))
        self._run("commit", "-m", message)
        return self.head_sha()

    def update_ref(self, ref_name: str, commit_sha: str) -> None:
        """Move one Runtime-owned ref to a validated Git commit."""
        if not ref_name.startswith("refs/agentplanex/"):
            raise ValueError("Only refs/agentplanex/* may be updated")
        self._run("rev-parse", "--verify", f"{commit_sha}^{{commit}}")
        self._run("update-ref", ref_name, commit_sha)

    def resolve_ref(self, ref_name: str) -> str:
        """Resolve one existing ref to its exact commit SHA."""
        return self._run("rev-parse", "--verify", f"{ref_name}^{{commit}}").stdout.strip()

    def integrate_fast_forward(
        self,
        candidate_commit_sha: str,
        *,
        expected_branch: str,
        expected_head: str,
    ) -> str:
        """Fast-forward the clean target branch to one fixed Candidate."""
        if self.current_branch() != expected_branch:
            raise GitRepositoryError("Project target branch changed during delivery")
        if self.head_sha() != expected_head:
            raise GitRepositoryError("Project target HEAD changed during delivery")
        self.assert_clean()
        self._run("merge", "--ff-only", candidate_commit_sha)
        integrated = self.head_sha()
        if integrated != candidate_commit_sha:
            raise GitRepositoryError("Candidate integration produced an unexpected HEAD")
        return integrated

    def _relative_path(self, path: Path) -> str:
        try:
            return str(path.resolve().relative_to(self.project_path.resolve()))
        except ValueError as error:
            raise ValueError(f"Git path is outside the project: {path}") from error

    @staticmethod
    def _require_identifier(name: str, value: str) -> None:
        allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
        if not value or any(character not in allowed for character in value):
            raise ValueError(f"{name} contains unsupported characters: {value!r}")

    def _run(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        result = self._run_unchecked(*arguments)
        if result.returncode != 0:
            self._raise_failed(result, arguments)
        return result

    def _run_unchecked(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(self.project_path), *arguments],
            check=False,
            capture_output=True,
            text=True,
        )

    @staticmethod
    def _raise_failed(
        result: subprocess.CompletedProcess[str],
        arguments: tuple[str, ...],
    ) -> None:
        detail = result.stderr.strip() or result.stdout.strip()
        raise GitRepositoryError(f"git {' '.join(arguments)} failed: {detail}")
