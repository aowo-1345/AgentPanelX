"""Read-only aggregation of Feature Runtime states for one Project."""

from dataclasses import dataclass

from agentplanex.domains import (
    BoardFeature,
    FeatureBinding,
    ProjectBoard,
)
from agentplanex.infrastructure.workspace_git import WorkspaceGit
from agentplanex.infrastructure.workspace_registry import WorkspaceRegistry
from agentplanex.services.feature_runtime_context import FeatureRuntimeContextQuery


@dataclass(frozen=True, slots=True)
class WorkspaceBoardQuery:
    """Compose Registry, Runtime SQLite, and Git facts without writing them."""

    registry: WorkspaceRegistry
    git: WorkspaceGit
    runtime_contexts: FeatureRuntimeContextQuery

    def get(self, project_id: str) -> ProjectBoard:
        project = self.registry.get_project(project_id)
        features = tuple(
            self._board_feature(binding)
            for binding in self.registry.list_features(project.project_id)
        )
        return ProjectBoard(
            project_id=project.project_id,
            name=project.name,
            features=features,
        )

    def _board_feature(self, binding: FeatureBinding) -> BoardFeature:
        context = self.runtime_contexts.get(binding)
        return BoardFeature(
            triage_id=binding.triage_id,
            project_id=binding.project_id,
            name=binding.name,
            status=context.status,
            branch=self.git.current_branch(binding.worktree_path),
            worktree_path=binding.worktree_path,
        )
