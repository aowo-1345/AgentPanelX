"""Git operations used by the user-level Project and Feature workspace."""

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


class WorkspaceGitError(RuntimeError):
    """A Project registration or Feature worktree Git operation failed."""


@dataclass(frozen=True, slots=True)
class GitProjectIdentity:
    repository_path: Path
    git_common_dir: Path


@dataclass(frozen=True, slots=True)
class WorkspaceGit:
    """Inspect registered repositories and create attached Feature worktrees."""

    def identify(self, repository_path: Path) -> GitProjectIdentity:
        path = repository_path.expanduser().resolve()
        if not path.is_dir():
            raise WorkspaceGitError(f"Git repository is not a directory: {path}")
        top_level = Path(self._run(path, "rev-parse", "--show-toplevel")).resolve()
        common_dir = Path(
            self._run(
                path,
                "rev-parse",
                "--path-format=absolute",
                "--git-common-dir",
            )
        ).resolve()
        return GitProjectIdentity(
            repository_path=top_level,
            git_common_dir=common_dir,
        )

    def local_branch_commit(self, repository_path: Path, branch: str) -> str:
        branch_name = branch.strip()
        if not branch_name:
            raise ValueError("Project main branch must not be empty")
        self._run(repository_path, "check-ref-format", "--branch", branch_name)
        return self._run(
            repository_path,
            "rev-parse",
            "--verify",
            f"refs/heads/{branch_name}^{{commit}}",
        )

    def create_feature_worktree(
        self,
        repository_path: Path,
        *,
        worktree_path: Path,
        branch: str,
        commit_sha: str,
    ) -> None:
        worktree_path.parent.mkdir(parents=True, exist_ok=True)
        self._run(
            repository_path,
            "worktree",
            "add",
            "-b",
            branch,
            str(worktree_path),
            commit_sha,
        )

    def current_branch(self, worktree_path: Path) -> str:
        branch = self._run(worktree_path, "branch", "--show-current")
        if not branch:
            raise WorkspaceGitError(f"Feature worktree is detached: {worktree_path}")
        return branch

    def remove_feature_worktree(
        self,
        repository_path: Path,
        *,
        worktree_path: Path,
    ) -> None:
        """Remove one registered linked worktree, including uncommitted changes."""
        target = worktree_path.resolve()
        registered = {
            Path(line.removeprefix("worktree ")).resolve()
            for line in self._run(
                repository_path,
                "worktree",
                "list",
                "--porcelain",
                "-z",
            ).split("\0")
            if line.startswith("worktree ")
        }
        if target not in registered:
            if target.exists():
                raise WorkspaceGitError(
                    "Refusing to remove a directory that is not a registered Git "
                    f"worktree: {target}"
                )
            return

        runtime_path = target / ".agentplanex"
        quarantine: Path | None = None
        runtime_backup: Path | None = None
        if runtime_path.exists():
            quarantine = Path(
                tempfile.mkdtemp(
                    prefix=f".{target.name}-runtime-delete-",
                    dir=target.parent,
                )
            )
            runtime_backup = quarantine / ".agentplanex"
            runtime_path.rename(runtime_backup)
        try:
            self._run(repository_path, "worktree", "remove", "--force", str(target))
        except Exception:
            if runtime_backup is not None and runtime_backup.exists():
                runtime_backup.rename(runtime_path)
            if quarantine is not None:
                quarantine.rmdir()
            raise
        if quarantine is not None:
            shutil.rmtree(quarantine)

    @staticmethod
    def _run(repository_path: Path, *arguments: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(repository_path), *arguments],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise WorkspaceGitError(
                f"git {' '.join(arguments)} failed for {repository_path}: {detail}"
            )
        return result.stdout.strip()
