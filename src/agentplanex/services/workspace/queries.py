"""Read-only aggregation of Registry, Feature SQLite, and Git facts."""

import sqlite3
from dataclasses import dataclass, field

from agentplanex.domains import (
    BoardFeature,
    FeatureBinding,
    ManagedProject,
    ProjectBoard,
    ProjectRuntimeContext,
    StageRun,
)
from agentplanex.infrastructure.git_repository import GitRepository
from agentplanex.infrastructure.sqlite import SQLiteDatabase
from agentplanex.infrastructure.sqlite.repositories import (
    SQLiteProjectRuntimeContextRepository,
    SQLiteStageRunRepository,
)
from agentplanex.infrastructure.workspace_git import WorkspaceGit
from agentplanex.infrastructure.workspace_registry import WorkspaceRegistry
from agentplanex.services.project_workspace import (
    ProjectWorkspaceQuery,
    ProjectWorkspaceView,
)


@dataclass(frozen=True, slots=True)
class FeatureWorkspaceView:
    """One Registry binding and its read-only Feature Runtime projection."""

    project: ManagedProject
    binding: FeatureBinding
    runtime_view: ProjectWorkspaceView


@dataclass(frozen=True, slots=True)
class WorkspaceQueries:
    """Compose Workspace views without constructing a ProjectRuntime or model."""

    registry: WorkspaceRegistry
    git: WorkspaceGit
    contexts: SQLiteProjectRuntimeContextRepository = field(
        default_factory=SQLiteProjectRuntimeContextRepository
    )
    stage_runs: SQLiteStageRunRepository = field(
        default_factory=SQLiteStageRunRepository
    )

    def project_board(self, project_id: str) -> ProjectBoard:
        project = self.registry.get_project(project_id)
        return ProjectBoard(
            project_id=project.project_id,
            name=project.name,
            features=tuple(
                self._board_feature(binding)
                for binding in self.registry.list_features(project.project_id)
            ),
        )

    def all_project_boards(self) -> tuple[ProjectBoard, ...]:
        return tuple(
            self.project_board(project.project_id)
            for project in self.registry.list_projects()
        )

    def feature_workspace(
        self,
        *,
        project_id: str,
        triage_id: str,
    ) -> FeatureWorkspaceView:
        binding = self.registry.get_feature(project_id, triage_id)
        project = self.registry.get_project(binding.project_id)
        runtime_view = ProjectWorkspaceQuery(
            database=SQLiteDatabase.for_project(binding.worktree_path),
            git=GitRepository(binding.worktree_path),
        ).get(binding.triage_id)
        return FeatureWorkspaceView(
            project=project,
            binding=binding,
            runtime_view=runtime_view,
        )

    def context(self, binding: FeatureBinding) -> ProjectRuntimeContext:
        database = SQLiteDatabase.for_project(binding.worktree_path)
        try:
            with database.read_only_connection() as connection:
                context = self.contexts.get(connection, binding.triage_id)
        except sqlite3.Error as error:
            raise LookupError(
                f"Feature Runtime database is unavailable: {binding.triage_id}"
            ) from error
        if context is None:
            raise LookupError(
                f"Feature Runtime Context not found: {binding.triage_id}"
            )
        return context

    def active_stage_run(self, binding: FeatureBinding) -> StageRun | None:
        database = SQLiteDatabase.for_project(binding.worktree_path)
        try:
            with database.read_only_connection() as connection:
                return self.stage_runs.get_active(connection, binding.triage_id)
        except sqlite3.Error as error:
            raise LookupError(
                f"Feature Runtime database is unavailable: {binding.triage_id}"
            ) from error

    def _board_feature(self, binding: FeatureBinding) -> BoardFeature:
        context = self.context(binding)
        return BoardFeature(
            triage_id=binding.triage_id,
            project_id=binding.project_id,
            name=binding.name,
            status=context.status,
            branch=self.git.current_branch(binding.worktree_path),
            worktree_path=binding.worktree_path,
            pending_action=context.pending_action,
            current_milestone_key=context.current_milestone_key,
            current_stage_key=context.current_stage_key,
        )
