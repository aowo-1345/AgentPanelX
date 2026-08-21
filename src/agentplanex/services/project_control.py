"""Stable read model for headless and UI project-control clients."""

from dataclasses import dataclass, field

from agentplanex.domains import (
    ExecutionEvent,
    MilestoneSnapshot,
    OwnerActivation,
    OwnerActivationStatus,
    ProjectRuntimeState,
    StageRun,
    StageRunStatus,
)
from agentplanex.infrastructure.git_repository import GitRepository
from agentplanex.infrastructure.sqlite import SQLiteDatabase
from agentplanex.infrastructure.sqlite.repositories import (
    SQLiteExecutionEventRepository,
    SQLiteMilestoneSnapshotRepository,
    SQLiteOwnerActivationRepository,
    SQLiteProjectRuntimeStateRepository,
    SQLiteStageRunRepository,
)


@dataclass(frozen=True, slots=True)
class ProjectControlView:
    """One composed projection over existing Runtime, Git, and Timeline facts."""

    state: ProjectRuntimeState
    snapshot: MilestoneSnapshot | None
    stage_runs: tuple[StageRun, ...]
    owner_activation: OwnerActivation | None
    timeline: tuple[ExecutionEvent, ...]
    git_branch: str
    git_head: str
    allowed_actions: tuple[str, ...]


@dataclass(slots=True)
class ProjectControlQuery:
    """Build a view without making business decisions or writing state."""

    database: SQLiteDatabase
    git: GitRepository
    states: SQLiteProjectRuntimeStateRepository = field(
        default_factory=SQLiteProjectRuntimeStateRepository
    )
    snapshots: SQLiteMilestoneSnapshotRepository = field(
        default_factory=SQLiteMilestoneSnapshotRepository
    )
    stage_runs: SQLiteStageRunRepository = field(
        default_factory=SQLiteStageRunRepository
    )
    activations: SQLiteOwnerActivationRepository = field(
        default_factory=SQLiteOwnerActivationRepository
    )
    events: SQLiteExecutionEventRepository = field(
        default_factory=SQLiteExecutionEventRepository
    )
    history_limit: int = 50

    def __post_init__(self) -> None:
        if self.history_limit <= 0:
            raise ValueError("Project Control history limit must be positive")

    def get(self, triage_id: str) -> ProjectControlView:
        with self.database.read_only_connection() as connection:
            state = self.states.get(connection, triage_id)
            if state is None:
                raise LookupError(f"Project Runtime State not found: {triage_id}")
            snapshot = (
                self.snapshots.get(connection, state.current_snapshot_id)
                if state.current_snapshot_id is not None
                else None
            )
            stage_runs = self.stage_runs.list_by_triage_id(connection, triage_id)
            activation = self.activations.get_unfinished(connection, triage_id)
            timeline = self.events.list_by_triage_id(connection, triage_id)
            active_stage = self.stage_runs.get_active(connection, triage_id)
        branch = self.git.current_branch()
        head = self.git.head_sha()
        return ProjectControlView(
            state=state,
            snapshot=snapshot,
            stage_runs=stage_runs[-self.history_limit :],
            owner_activation=activation,
            timeline=timeline[-self.history_limit :],
            git_branch=branch,
            git_head=head,
            allowed_actions=_allowed_actions(state, activation, active_stage),
        )

    def get_current(self) -> ProjectControlView:
        """Read the sole Feature projection without constructing command Context."""
        with self.database.read_only_connection() as connection:
            states = self.states.list_all(connection)
        if not states:
            raise LookupError("Project Runtime State is not initialized")
        if len(states) != 1:
            raise RuntimeError("Project database contains multiple Runtime States")
        return self.get(states[0].triage_id)


def _allowed_actions(
    state: ProjectRuntimeState,
    activation: OwnerActivation | None,
    active_stage: StageRun | None,
) -> tuple[str, ...]:
    if activation is not None and activation.status in {
        OwnerActivationStatus.PENDING,
        OwnerActivationStatus.RUNNING,
    }:
        return ("drive",)
    if active_stage is not None and active_stage.status in {
        StageRunStatus.QUEUED,
        StageRunStatus.RUNNING,
    }:
        return ("drive-delivery",)
    actions = ["message"]
    if state.pending_action == "PLAN_APPROVAL":
        actions.extend(("approve", "reject"))
    elif state.pending_action == "FIRST_RUN_APPROVAL":
        actions.append("start")
    return tuple(actions)
