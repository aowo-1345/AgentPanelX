"""Persisted context models owned by the Project Owner Agent."""

from dataclasses import dataclass

from agentplanex.project_owner_agent.contracts import Message


@dataclass(frozen=True, slots=True)
class MessageHistory:
    """One append-only batch of messages for a Project Owner session."""

    project_owner_session_id: str
    message_id: str
    sequence: int
    message: tuple[Message, ...]

    def __post_init__(self) -> None:
        if self.sequence <= 0:
            raise ValueError("message-history sequence must be positive")
        if not self.message:
            raise ValueError("message-history batch must not be empty")


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
