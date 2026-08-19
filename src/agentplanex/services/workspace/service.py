"""The sole Workspace interface used by Web and CLI adapters."""

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from agentplanex.domains import (
    FeatureAction,
    FeatureBinding,
    FeatureState,
    FeatureView,
    ManagedProject,
    OwnerActivation,
    ProjectBoard,
)
from agentplanex.infrastructure.workspace_git import WorkspaceGit
from agentplanex.infrastructure.workspace_registry import WorkspaceRegistry
from agentplanex.project_runtime import ProjectRuntime
from agentplanex.services.workspace.dispatcher import WorkspaceDispatcher
from agentplanex.services.workspace.queries import (
    FeatureWorkspaceView,
    WorkspaceQueries,
)

_UNSAFE_SLUG = re.compile(r"[^a-z0-9]+")


def _noop() -> None:
    pass


@dataclass(slots=True)
class WorkspaceService:
    """Locate Feature Runtimes and own every external Workspace command."""

    data_home: Path
    registry: WorkspaceRegistry
    git: WorkspaceGit
    queries: WorkspaceQueries
    dispatcher: WorkspaceDispatcher
    runtime_factory: Callable[[Path], ProjectRuntime]
    close_resources: Callable[[], None] = _noop

    def start(self) -> int:
        """Terminalize interrupted work without driving any old task."""
        failed_features = 0
        for project in self.registry.list_projects():
            for binding in self.registry.list_features(project.project_id):
                if self.runtime_factory(binding.worktree_path).fail_interrupted_work():
                    failed_features += 1
        return failed_features

    def close(self) -> None:
        try:
            self.dispatcher.close()
        finally:
            self.close_resources()

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

    def project_git_version(self, project: ManagedProject) -> str:
        return self.git.local_branch_commit(
            project.repository_path,
            project.main_branch,
        )

    def refresh_projects(self) -> tuple[ManagedProject, ...]:
        projects = self.registry.list_projects()
        for project in projects:
            self.project_git_version(project)
        return projects

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
        runtime = self.runtime_factory(binding.worktree_path)

        def begin() -> FeatureState:
            context = runtime.begin_feature(binding.triage_id)
            return FeatureState(
                project_id=binding.project_id,
                triage_id=binding.triage_id,
                status=context.status,
            )

        return self.dispatcher.exclusive(binding.triage_id, begin)

    def submit_feature_message(
        self,
        *,
        project_id: str,
        triage_id: str,
        content: str,
    ) -> OwnerActivation:
        binding = self._require_feature_binding(project_id, triage_id)
        runtime = self.runtime_factory(binding.worktree_path)
        return self.dispatcher.dispatch(
            binding.triage_id,
            persist=lambda: runtime.submit_message(content),
            drive=runtime.drive_until_waiting,
        )

    def project_board(self, project_id: str) -> ProjectBoard:
        return self.queries.project_board(_required_text("Project ID", project_id))

    def all_project_boards(self) -> tuple[ProjectBoard, ...]:
        return self.queries.all_project_boards()

    def feature_workspace(
        self,
        *,
        project_id: str,
        triage_id: str,
    ) -> FeatureWorkspaceView:
        return self.queries.feature_workspace(
            project_id=_required_text("Project ID", project_id),
            triage_id=_required_text("Feature Triage ID", triage_id),
        )

    def perform_feature_action(
        self,
        *,
        project_id: str,
        triage_id: str,
        action: FeatureAction,
        feedback: str = "",
    ) -> FeatureWorkspaceView:
        binding = self._require_feature_binding(project_id, triage_id)
        runtime = self.runtime_factory(binding.worktree_path)
        if action is FeatureAction.BEGIN:
            return self.dispatcher.exclusive(
                binding.triage_id,
                lambda: self._begin_and_read(binding, runtime),
            )
        if action is FeatureAction.REJECT_PLAN and not feedback.strip():
            raise ValueError("Plan rejection feedback must not be empty")
        if action not in {
            FeatureAction.APPROVE_PLAN,
            FeatureAction.REJECT_PLAN,
            FeatureAction.START_DELIVERY,
        }:
            raise ValueError(f"Unsupported Feature action: {action}")
        self.dispatcher.dispatch(
            binding.triage_id,
            persist=lambda: self._persist_action(
                runtime,
                action,
                feedback,
            ),
            drive=runtime.drive_until_waiting,
        )
        return self.queries.feature_workspace(
            project_id=binding.project_id,
            triage_id=binding.triage_id,
        )

    def delete_feature(self, *, project_id: str, triage_id: str) -> None:
        binding = self._require_feature_binding(project_id, triage_id)
        self.dispatcher.exclusive(
            binding.triage_id,
            lambda: self._delete_feature(binding),
        )

    def _begin_and_read(
        self,
        binding: FeatureBinding,
        runtime: ProjectRuntime,
    ) -> FeatureWorkspaceView:
        runtime.begin_feature(binding.triage_id)
        return self.queries.feature_workspace(
            project_id=binding.project_id,
            triage_id=binding.triage_id,
        )

    def _persist_action(
        self,
        runtime: ProjectRuntime,
        action: FeatureAction,
        feedback: str,
    ) -> None:
        if action is FeatureAction.APPROVE_PLAN:
            runtime.approve_plan()
        elif action is FeatureAction.REJECT_PLAN:
            runtime.reject_plan(feedback)
        elif action is FeatureAction.START_DELIVERY:
            runtime.start_first_run()
        else:
            raise AssertionError(f"Non-automatic Feature action: {action}")

    def _delete_feature(self, binding: FeatureBinding) -> None:
        project = self.registry.get_project(binding.project_id)
        worktree_path = _managed_feature_path(
            self.data_home,
            binding.project_id,
            binding.worktree_path,
        )
        if worktree_path == project.repository_path.resolve():
            raise ValueError("Refusing to remove the registered Project repository")
        if worktree_path.exists():
            runtime_view = self.queries.feature_workspace(
                project_id=binding.project_id,
                triage_id=binding.triage_id,
            ).runtime_view
            if runtime_view.owner_activation is not None:
                raise ValueError(
                    "Feature cannot be deleted while a Project Owner activation "
                    "is pending or running"
                )
            if self.queries.active_stage_run(binding) is not None:
                raise ValueError(
                    "Feature cannot be deleted while Delivery is queued or running"
                )
        self.git.remove_feature_worktree(
            project.repository_path,
            worktree_path=worktree_path,
        )
        self.registry.delete_feature(binding.project_id, binding.triage_id)

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


def _managed_feature_path(
    data_home: Path,
    project_id: str,
    worktree_path: Path,
) -> Path:
    project_root = (data_home.resolve() / "projects" / project_id).resolve()
    target = worktree_path.resolve()
    if target.parent != project_root:
        raise ValueError(
            "Refusing to remove a Feature outside its configured Workspace data "
            f"directory: {target}"
        )
    return target


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
