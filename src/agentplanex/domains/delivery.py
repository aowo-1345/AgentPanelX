"""Typed delivery facts owned by the Project Runtime."""

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum


class MilestoneState(StrEnum):
    """A Milestone's state inside one immutable Snapshot."""

    PENDING = "pending"
    COMPLETED = "completed"


class StageRunStatus(StrEnum):
    """Lifecycle of one durable Stage execution request."""

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class Stage:
    """One ordered implementation unit inside a Milestone."""

    key: str
    objective: str

    def __post_init__(self) -> None:
        _require_identifier("stage key", self.key)
        _require_text("stage objective", self.objective)


@dataclass(frozen=True, slots=True)
class Milestone:
    """One ordered delivery objective and its ordered Stages."""

    key: str
    objective: str
    state: MilestoneState
    stages: tuple[Stage, ...]

    def __post_init__(self) -> None:
        _require_identifier("milestone key", self.key)
        _require_text("milestone objective", self.objective)
        if not self.stages:
            raise ValueError("Milestone must contain at least one Stage")
        _require_unique("stage", (stage.key for stage in self.stages))


@dataclass(frozen=True, slots=True)
class MilestoneSnapshot:
    """An immutable, complete Milestone View bound to an approved Plan."""

    snapshot_id: str
    triage_id: str
    previous_snapshot_id: str | None
    plan_commit_sha: str
    milestones: tuple[Milestone, ...]
    reason: str
    message_id: str | None
    created_at: datetime

    def __post_init__(self) -> None:
        _require_text("snapshot_id", self.snapshot_id)
        _require_text("triage_id", self.triage_id)
        _require_text("plan_commit_sha", self.plan_commit_sha)
        _require_text("snapshot reason", self.reason)
        if not self.milestones:
            raise ValueError("Milestone Snapshot must contain at least one Milestone")
        _require_unique("milestone", (milestone.key for milestone in self.milestones))

    def first_pending(self) -> Milestone | None:
        """Return the only Milestone selection policy used by Delivery."""
        return next(
            (
                milestone
                for milestone in self.milestones
                if milestone.state is MilestoneState.PENDING
            ),
            None,
        )

    def with_completed_milestone(
        self,
        milestone_key: str,
        *,
        snapshot_id: str,
        reason: str,
        message_id: str | None,
        created_at: datetime | None = None,
    ) -> "MilestoneSnapshot":
        """Create the successor Snapshot after the only legal completion path."""
        found = False
        milestones: list[Milestone] = []
        for milestone in self.milestones:
            if milestone.key == milestone_key:
                found = True
                if milestone.state is not MilestoneState.PENDING:
                    raise ValueError(f"Milestone is not pending: {milestone_key}")
                milestones.append(
                    replace(milestone, state=MilestoneState.COMPLETED)
                )
            else:
                milestones.append(milestone)
        if not found:
            raise LookupError(f"Milestone not found in Snapshot: {milestone_key}")
        return MilestoneSnapshot(
            snapshot_id=snapshot_id,
            triage_id=self.triage_id,
            previous_snapshot_id=self.snapshot_id,
            plan_commit_sha=self.plan_commit_sha,
            milestones=tuple(milestones),
            reason=reason,
            message_id=message_id,
            created_at=created_at or datetime.now(UTC),
        )


@dataclass(frozen=True, slots=True)
class StageRun:
    """One recoverable request to execute a fixed Stage from a fixed Git input."""

    stage_run_id: str
    triage_id: str
    run_id: str
    snapshot_id: str
    milestone_key: str
    stage_key: str
    status: StageRunStatus
    input_commit_sha: str
    output_commit_sha: str | None
    failure: str | None
    created_at: datetime
    started_at: datetime | None = None
    lease_expires_at: datetime | None = None
    finished_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in (
            "stage_run_id",
            "triage_id",
            "run_id",
            "snapshot_id",
            "milestone_key",
            "stage_key",
            "input_commit_sha",
        ):
            _require_text(name, str(getattr(self, name)))
        if self.status is StageRunStatus.QUEUED:
            if any(
                value is not None
                for value in (
                    self.started_at,
                    self.lease_expires_at,
                    self.finished_at,
                    self.output_commit_sha,
                    self.failure,
                )
            ):
                raise ValueError("Queued StageRun cannot have lifecycle results")
            return
        if self.status is StageRunStatus.RUNNING:
            if self.started_at is None:
                raise ValueError("Running StageRun must have started_at")
            if self.lease_expires_at is None:
                raise ValueError("Running StageRun must have lease_expires_at")
            if any(
                value is not None
                for value in (self.finished_at, self.output_commit_sha, self.failure)
            ):
                raise ValueError("Running StageRun cannot have terminal results")
            return
        if self.finished_at is None:
            raise ValueError("Finished StageRun must have finished_at")
        if self.lease_expires_at is not None:
            raise ValueError("Finished StageRun cannot retain a lease")
        if self.status is StageRunStatus.SUCCEEDED:
            if self.started_at is None:
                raise ValueError("Succeeded StageRun must have started_at")
            _require_text("output_commit_sha", self.output_commit_sha or "")
            if self.failure is not None:
                raise ValueError("Succeeded StageRun cannot contain a failure")
            return
        if self.output_commit_sha is not None:
            raise ValueError("Failed StageRun cannot contain an output commit")
        _require_text("StageRun failure", self.failure or "")


def milestone_view_json(milestones: tuple[Milestone, ...]) -> str:
    """Return the canonical representation stored in a Snapshot and reviewed by Gates."""
    return json.dumps(
        [
            {
                "key": milestone.key,
                "objective": milestone.objective,
                "state": milestone.state.value,
                "stages": [
                    {"key": stage.key, "objective": stage.objective}
                    for stage in milestone.stages
                ],
            }
            for milestone in milestones
        ],
        ensure_ascii=True,
        separators=(",", ":"),
    )


def milestone_view_digest(milestones: tuple[Milestone, ...]) -> str:
    """Return a stable identity for one exact complete Milestone View."""
    return hashlib.sha256(milestone_view_json(milestones).encode("utf-8")).hexdigest()


def delivery_run_ref(run_id: str) -> str:
    """Return the durable ref for a Run's latest materialized Stage output."""
    _require_identifier("run_id", run_id)
    return f"refs/agentplanex/runs/{run_id}"


def delivery_candidate_ref(run_id: str) -> str:
    """Return the ref that keeps one fixed Candidate reachable."""
    _require_identifier("run_id", run_id)
    return f"refs/agentplanex/candidates/{run_id}"


def _require_text(name: str, value: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} must not be empty")


def _require_identifier(name: str, value: str) -> None:
    _require_text(name, value)
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
    if any(character not in allowed for character in value):
        raise ValueError(f"{name} contains unsupported characters: {value!r}")


def _require_unique(kind: str, values: Iterable[str]) -> None:
    keys: tuple[str, ...] = tuple(values)
    if len(keys) != len(set(keys)):
        raise ValueError(f"Duplicate {kind} key")
