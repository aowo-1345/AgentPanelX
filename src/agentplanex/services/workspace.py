"""User-level Project registration and Feature Runtime provisioning."""

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from agentplanex.domains import (
    FeatureBinding,
    FeatureState,
    FeatureView,
    ManagedProject,
    OwnerActivation,
    ProjectBoard,
    ProjectRuntimeContext,
)
from agentplanex.infrastructure.workspace_git import WorkspaceGit
from agentplanex.infrastructure.workspace_registry import WorkspaceRegistry
from agentplanex.services.feature_runtime_context import FeatureRuntimeContextQuery
from agentplanex.services.workspace_board import WorkspaceBoardQuery

_UNSAFE_SLUG = re.compile(r"[^a-z0-9]+")


class FeatureRuntime(Protocol):
    """Existing Runtime capabilities routed through a Feature binding."""

    def initialize(self) -> ProjectRuntimeContext: ...

    def begin_feature(self, triage_id: str) -> ProjectRuntimeContext: ...

    def submit_message(self, content: str) -> OwnerActivation: ...


type RuntimeFactory = Callable[[Path], FeatureRuntime]


@dataclass(slots=True)
class WorkspaceService:
    """Hide Registry, Git, and Runtime coordination behind user operations."""

    data_home: Path
    registry: WorkspaceRegistry
    git: WorkspaceGit
    board_query: WorkspaceBoardQuery
    runtime_contexts: FeatureRuntimeContextQuery
    runtime_factory: RuntimeFactory

    def register_project(
        self,
        *,
        name: str,
        repository_path: Path,
        main_branch: str,
    ) -> ManagedProject:
        project_name = _required_text("Project name", name)
        identity = self.git.identify(repository_path)
        branch = _required_text("Project main branch", main_branch)
        self.git.local_branch_commit(identity.repository_path, branch)
        existing = self.registry.find_project_by_common_dir(identity.git_common_dir)
        if existing is not None:
            raise ValueError(
                "Git repository is already registered as Project "
                f"{existing.project_id}"
            )
        project = ManagedProject(
            project_id=uuid4().hex,
            name=project_name,
            repository_path=identity.repository_path,
            git_common_dir=identity.git_common_dir,
            main_branch=branch,
        )
        self.registry.insert_project(project)
        return project

    def list_projects(self) -> tuple[ManagedProject, ...]:
        return self.registry.list_projects()

    def create_feature(self, *, project_id: str, name: str) -> FeatureView:
        project = self.registry.get_project(_required_text("Project ID", project_id))
        feature_name = _required_text("Feature name", name)
        commit_sha = self.git.local_branch_commit(
            project.repository_path,
            project.main_branch,
        )
        suffix = uuid4().hex[:12]
        slug = _feature_slug(feature_name)
        branch = f"agentplanex/{slug}-{suffix}"
        worktree_path = (
            self.data_home.resolve()
            / "projects"
            / project.project_id
            / f"{slug}-{suffix}"
        )
        self.git.create_feature_worktree(
            project.repository_path,
            worktree_path=worktree_path,
            branch=branch,
            commit_sha=commit_sha,
        )
        context = self.runtime_factory(worktree_path).initialize()
        binding = FeatureBinding(
            triage_id=context.triage_id,
            project_id=project.project_id,
            name=feature_name,
            worktree_path=worktree_path,
        )
        self.registry.insert_feature(binding)
        return _feature_view(binding, branch)

    def list_features(self, project_id: str) -> tuple[FeatureView, ...]:
        bindings = self.registry.list_features(_required_text("Project ID", project_id))
        return tuple(
            _feature_view(binding, self.git.current_branch(binding.worktree_path))
            for binding in bindings
        )

    def begin_feature(self, *, project_id: str, triage_id: str) -> FeatureState:
        binding = self._require_feature_binding(project_id, triage_id)
        context = self.runtime_factory(binding.worktree_path).begin_feature(
            binding.triage_id
        )
        return FeatureState(
            project_id=binding.project_id,
            triage_id=binding.triage_id,
            status=context.status,
        )

    def submit_feature_message(
        self,
        *,
        project_id: str,
        triage_id: str,
        content: str,
    ) -> OwnerActivation:
        binding = self._require_feature_binding(project_id, triage_id)
        self.runtime_contexts.get(binding)
        return self.runtime_factory(binding.worktree_path).submit_message(content)

    def project_board(self, project_id: str) -> ProjectBoard:
        return self.board_query.get(_required_text("Project ID", project_id))

    def _require_feature_binding(
        self,
        project_id: str,
        triage_id: str,
    ) -> FeatureBinding:
        return self.registry.get_feature(
            _required_text("Project ID", project_id),
            _required_text("Feature Triage ID", triage_id),
        )


def _required_text(label: str, value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{label} must not be empty")
    return normalized


def _feature_slug(name: str) -> str:
    slug = _UNSAFE_SLUG.sub("-", name.lower()).strip("-")
    return (slug or "feature")[:48]


def _feature_view(binding: FeatureBinding, branch: str) -> FeatureView:
    return FeatureView(
        triage_id=binding.triage_id,
        project_id=binding.project_id,
        name=binding.name,
        branch=branch,
        worktree_path=binding.worktree_path,
    )
