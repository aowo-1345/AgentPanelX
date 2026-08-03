"""Project runtime context domain object."""

from dataclasses import dataclass

from agentplanex.domains.project_owner_agent import ProjectOwnerAgent


@dataclass(frozen=True, slots=True)
class ProjectRuntimeContext:
    """Runtime state for one Triage and its Project Owner Agent."""

    triage_id: str
    status: str | None = None
    git_main_version: str | None = None
    project_owner_agent: ProjectOwnerAgent | None = None

    def __post_init__(self) -> None:
        if not self.triage_id.strip():
            raise ValueError("triage_id must not be empty")
