"""Plan approval workflow over project Specs, Git, and Runtime state."""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

from agentplanex.domains import ProjectRuntimeContext
from agentplanex.infrastructure.git_repository import GitRepository
from agentplanex.infrastructure.sqlite import SQLiteDatabase
from agentplanex.infrastructure.sqlite.repositories import (
    SQLiteProjectRuntimeContextRepository,
)

SPEC_DOCUMENT_NAMES = ("architecture.md", "requirements.md", "roadmap.md")
type PlanHardGate = Callable[[tuple[Path, ...]], None]


class PlanningError(ValueError):
    """An expected planning error that the Project Owner can correct."""


def pass_plan_hard_gate(_spec_documents: tuple[Path, ...]) -> None:
    """Default Plan gate until an external reviewer is connected."""


@dataclass(frozen=True, slots=True)
class PlanDecision:
    context: ProjectRuntimeContext
    resume_message: str
    commit_sha: str | None = None


@dataclass(slots=True)
class PlanningService:
    project_path: Path
    database: SQLiteDatabase
    contexts: SQLiteProjectRuntimeContextRepository = field(
        default_factory=SQLiteProjectRuntimeContextRepository
    )
    git: GitRepository | None = None
    review_plan: PlanHardGate = pass_plan_hard_gate

    def __post_init__(self) -> None:
        if self.git is None:
            self.git = GitRepository(self.project_path)

    @classmethod
    def for_project(cls, project_path: Path) -> "PlanningService":
        return cls(
            project_path=project_path,
            database=SQLiteDatabase.for_project(project_path),
        )

    def request_plan_approval(
        self,
        context: ProjectRuntimeContext,
    ) -> ProjectRuntimeContext:
        spec_documents = self._spec_documents()
        self.review_plan(spec_documents)

        with self.database.transaction() as connection:
            current = self._get_context(connection, context.triage_id)
            if current.pending_action is not None:
                raise PlanningError(
                    "Project already has a pending action: "
                    f"{current.pending_action}"
                )
            if current.status not in {"TRIAGE", "TODO", "IN_PROGRESS"}:
                raise PlanningError(
                    "Plan approval cannot be requested from status "
                    f"{current.status}"
                )

            status = "BLOCKED" if current.status == "IN_PROGRESS" else "TODO"
            updated = replace(
                current,
                status=status,
                pending_action="PLAN_APPROVAL",
            )
            self.contexts.update(connection, updated)
        return updated

    def approve_plan(self, triage_id: str) -> PlanDecision:
        spec_documents = self._spec_documents()
        self._assert_plan_pending(triage_id)
        git = self.git
        if git is None:
            raise RuntimeError("Planning Service has no Git repository")
        commit_sha = git.commit_paths(
            spec_documents,
            message="plan: approve specifications",
        )

        with self.database.transaction() as connection:
            current = self._get_context(connection, triage_id)
            self._assert_pending_action(current)
            updated = replace(
                current,
                status=(
                    "IN_PROGRESS"
                    if current.rolling_started_at is not None
                    else "TODO"
                ),
                pending_action=None,
                current_plan_commit_sha=commit_sha,
            )
            self.contexts.update(connection, updated)

        return PlanDecision(
            context=updated,
            commit_sha=commit_sha,
            resume_message=(
                "The user approved the current Plan. "
                f"The approved Plan commit is {commit_sha}."
            ),
        )

    def reject_plan(self, triage_id: str, feedback: str = "") -> PlanDecision:
        with self.database.transaction() as connection:
            current = self._get_context(connection, triage_id)
            self._assert_pending_action(current)
            updated = replace(
                current,
                status=(
                    "IN_PROGRESS"
                    if current.rolling_started_at is not None
                    else "TODO"
                ),
                pending_action=None,
            )
            self.contexts.update(connection, updated)

        resume_message = "The user rejected the current Plan."
        if feedback.strip():
            resume_message = f"{resume_message} Feedback: {feedback.strip()}"
        return PlanDecision(context=updated, resume_message=resume_message)

    def _spec_documents(self) -> tuple[Path, ...]:
        paths = tuple(self.project_path / name for name in SPEC_DOCUMENT_NAMES)
        missing = tuple(path.name for path in paths if not path.is_file())
        if missing:
            raise PlanningError(
                "Missing Plan specification documents: " + ", ".join(missing)
            )
        return paths

    def _assert_plan_pending(self, triage_id: str) -> None:
        with self.database.connection() as connection:
            current = self._get_context(connection, triage_id)
        self._assert_pending_action(current)

    @staticmethod
    def _assert_pending_action(context: ProjectRuntimeContext) -> None:
        if context.pending_action != "PLAN_APPROVAL":
            raise PlanningError("Project is not waiting for Plan approval")

    def _get_context(
        self,
        connection: sqlite3.Connection,
        triage_id: str,
    ) -> ProjectRuntimeContext:
        context = self.contexts.get(connection, triage_id)
        if context is None:
            raise LookupError(f"Project Runtime Context not found: {triage_id}")
        return context
