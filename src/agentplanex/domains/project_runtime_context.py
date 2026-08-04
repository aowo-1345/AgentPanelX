"""Project runtime context domain object."""

from dataclasses import dataclass
from datetime import datetime

from agentplanex.domains.project_owner_agent import ProjectOwnerAgent


@dataclass(frozen=True, slots=True)
class ProjectRuntimeContext:
    """Runtime state for one Triage and its Project Owner Agent."""

    triage_id: str
    idea: str | None = None
    status: str = "TRIAGE"
    pending_action: str | None = None
    git_branch: str | None = None
    git_main_version: str | None = None
    rolling_started_at: datetime | None = None
    current_plan_commit_sha: str | None = None
    pending_plan_subject_digest: str | None = None
    current_snapshot_id: str | None = None
    current_run_id: str | None = None
    current_milestone_key: str | None = None
    current_stage_key: str | None = None
    current_candidate_commit_sha: str | None = None
    project_owner_agent: ProjectOwnerAgent | None = None

    def __post_init__(self) -> None:
        if not self.triage_id.strip():
            raise ValueError("triage_id must not be empty")
        if self.status not in {
            "TRIAGE",
            "TODO",
            "READY",
            "IN_PROGRESS",
            "BLOCKED",
            "DONE",
        }:
            raise ValueError(f"Unsupported project status: {self.status!r}")
        if self.pending_action not in {
            None,
            "PLAN_APPROVAL",
            "FIRST_RUN_APPROVAL",
        }:
            raise ValueError(
                f"Unsupported pending action: {self.pending_action!r}"
            )
