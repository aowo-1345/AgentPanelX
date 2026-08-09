"""Project Owner Agent summary history domain object."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SummaryHistory:
    """One persisted summary version for a Project Owner session."""

    project_owner_session_id: str
    summary_id: str
    covered_through_message_id: str
    intent_summary_content: str
    trajectory_summary_content: str

    def __post_init__(self) -> None:
        for field_name in (
            "project_owner_session_id",
            "summary_id",
            "covered_through_message_id",
            "intent_summary_content",
            "trajectory_summary_content",
        ):
            if not getattr(self, field_name).strip():
                raise ValueError(f"{field_name} must not be empty")
