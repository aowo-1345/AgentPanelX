"""Stable business contracts for the Delivery lifecycle."""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from agentplanex.domains.agent_collaboration import ArtifactDescriptor
from agentplanex.domains.delivery import (
    CandidateIdentity,
    Milestone,
    MilestoneSnapshot,
    Stage,
)
from agentplanex.domains.project_runtime_state import ProjectRuntimeState


class DeliveryError(ValueError):
    """An expected Delivery Contract error that the Owner can correct."""


class DeliveryWorkState(StrEnum):
    """The only Stage scheduling facts exposed to Runtime orchestration."""

    IDLE = "IDLE"
    RUNNABLE = "RUNNABLE"
    RUNNING = "RUNNING"


class DeliveryDriveOutcome(StrEnum):
    """The high-level result of driving at most one Stage."""

    IDLE = "idle"
    STAGE_SUCCEEDED = "stage_succeeded"
    CANDIDATE_READY = "candidate_ready"
    STAGE_FAILED = "stage_failed"


@dataclass(frozen=True, slots=True)
class MilestoneReviewRequest:
    """The exact complete Milestone View supplied to a protected review."""

    triage_id: str
    plan_commit_sha: str
    milestones: tuple[Milestone, ...]
    subject_digest: str


@dataclass(frozen=True, slots=True)
class MilestoneReviewResult:
    """Validated result required by the Milestone publication Hard Gate."""

    subject_digest: str
    decision: Literal["pass", "revise"]
    summary: str
    required_changes: tuple[str, ...]
    audit_artifact: ArtifactDescriptor


type MilestoneHardGate = Callable[[MilestoneReviewRequest], MilestoneReviewResult]


def missing_milestone_hard_gate(
    _request: MilestoneReviewRequest,
) -> MilestoneReviewResult:
    """Fail closed when no Milestone Gate is bound at composition time."""
    raise DeliveryError("Milestone Hard Gate is not configured")


@dataclass(frozen=True, slots=True)
class MilestonesUpdated:
    """Observable result of publishing one complete Milestone View."""

    state: ProjectRuntimeState
    snapshot: MilestoneSnapshot | None
    accepted: bool
    subject_digest: str
    review: MilestoneReviewResult | None


@dataclass(frozen=True, slots=True)
class FirstRunApprovalRequested:
    """The first Run is ready but still requires an explicit user Start."""

    state: ProjectRuntimeState
    snapshot: MilestoneSnapshot
    milestone: Milestone


@dataclass(frozen=True, slots=True)
class MilestoneRunQueued:
    """Identity receipt for one durably queued first Stage."""

    state: ProjectRuntimeState
    snapshot: MilestoneSnapshot
    milestone: Milestone
    stage: Stage
    run_id: str
    stage_run_id: str
    snapshot_id: str
    milestone_key: str
    stage_key: str
    input_commit_sha: str
    first_run: bool


@dataclass(frozen=True, slots=True)
class CandidateDecision:
    """The controlled outcome of accepting or rejecting one Candidate."""

    state: ProjectRuntimeState
    identity: CandidateIdentity
    decision: Literal["accept", "reject"]
    result_snapshot_id: str
    next_milestone_key: str | None
    completed: bool
