"""Project Owner Agent summary history domain object."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SummaryHistory:
    """One persisted summary version for a Project Owner session."""

    project_owner_session_id: str
    summary_id: str
    summary_content: str
