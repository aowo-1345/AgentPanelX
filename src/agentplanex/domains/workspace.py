"""User-managed Git Projects and their Feature Runtime bindings."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ManagedProject:
    """One registered Git repository with a fixed local main branch."""

    project_id: str
    name: str
    repository_path: Path
    git_common_dir: Path
    main_branch: str


@dataclass(frozen=True, slots=True)
class FeatureBinding:
    """Stable navigation from a user-visible Feature to one Runtime."""

    triage_id: str
    project_id: str
    name: str
    worktree_path: Path


@dataclass(frozen=True, slots=True)
class FeatureView:
    """One persisted Feature binding enriched with its live Git branch."""

    triage_id: str
    project_id: str
    name: str
    branch: str
    worktree_path: Path


@dataclass(frozen=True, slots=True)
class FeatureState:
    """The selected Feature identity and its current Runtime status."""

    project_id: str
    triage_id: str
    status: str


@dataclass(frozen=True, slots=True)
class BoardFeature:
    """One Feature card composed from Registry, Runtime, and Git facts."""

    triage_id: str
    project_id: str
    name: str
    status: str
    branch: str
    worktree_path: Path


@dataclass(frozen=True, slots=True)
class ProjectBoard:
    """Current Feature Runtime states grouped under one managed Project."""

    project_id: str
    name: str
    features: tuple[BoardFeature, ...]
