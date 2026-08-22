"""Canonical Plan identity over worktree and committed document bytes."""

from pathlib import Path

from agentplanex.infrastructure.git_repository import GitRepository, GitRepositoryError
from agentplanex.services.planning.contracts import PlanningError
from agentplanex.services.planning.models import PLAN_DOCUMENT_NAMES, PlanDocument, PlanSubject


def plan_document_paths(project_path: Path) -> tuple[Path, ...]:
    paths = tuple(project_path / name for name in PLAN_DOCUMENT_NAMES)
    missing = tuple(path.name for path in paths if not path.is_file())
    if missing:
        raise PlanningError("Missing Plan specification documents: " + ", ".join(missing))
    return paths


def freeze_worktree_subject(project_path: Path) -> PlanSubject:
    documents: list[PlanDocument] = []
    for path in plan_document_paths(project_path):
        try:
            content = path.read_bytes()
        except OSError as error:
            raise PlanningError(f"Cannot read Plan specification document: {path.name}") from error
        documents.append(PlanDocument(name=path.name, content=content))
    return PlanSubject(tuple(documents))


def freeze_commit_subject(
    project_path: Path,
    git: GitRepository,
    commit_sha: str,
) -> PlanSubject:
    documents: list[PlanDocument] = []
    try:
        for name in PLAN_DOCUMENT_NAMES:
            path = project_path / name
            documents.append(
                PlanDocument(
                    name=name,
                    content=git.read_path_at_commit(commit_sha, path),
                )
            )
    except GitRepositoryError as error:
        raise PlanningError(
            "Plan checkpoint does not contain the reviewed specification documents"
        ) from error
    return PlanSubject(tuple(documents))
