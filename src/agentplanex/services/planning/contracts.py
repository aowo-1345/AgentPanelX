"""Stable business contracts for the Plan approval lifecycle."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from agentplanex.domains.agent_collaboration import ArtifactDescriptor
from agentplanex.domains.project_runtime_state import ProjectRuntimeState
from agentplanex.services.planning.models import PlanSubject
from agentplanex.services.project_runtime_context.models import OwnerActivation


class PlanningError(ValueError):
    """An expected planning error that the Project Owner can correct."""


@dataclass(frozen=True, slots=True)
class PlanReviewRequest:
    """One immutable Plan subject supplied to a protected external review."""

    triage_id: str
    subject: PlanSubject


@dataclass(frozen=True, slots=True)
class PlanReviewResult:
    """The validated result required from the Plan Hard Gate Contract."""

    subject_digest: str
    decision: Literal["pass", "revise"]
    summary: str
    required_changes: tuple[str, ...]
    audit_artifact: ArtifactDescriptor


type PlanHardGate = Callable[[PlanReviewRequest], PlanReviewResult]


def missing_plan_hard_gate(_request: PlanReviewRequest) -> PlanReviewResult:
    """Fail closed when a Planning Service has no configured gate."""
    raise PlanningError("Plan Hard Gate is not configured")


@dataclass(frozen=True, slots=True)
class PlanDecision:
    state: ProjectRuntimeState
    activation: OwnerActivation
    commit_sha: str | None = None


@dataclass(frozen=True, slots=True)
class PlanApprovalRequest:
    """The observable result of submitting one exact Plan for human approval."""

    state: ProjectRuntimeState
    accepted: bool
    subject_digest: str
    review: PlanReviewResult | None
