"""Read-only reconstruction of one Project Owner message checkpoint."""

from dataclasses import dataclass

from agentplanex.domains.message_history import Message


@dataclass(frozen=True, slots=True)
class RestoredOwnerContext:
    """One deterministic Owner context projection through a message checkpoint."""

    triage_id: str
    project_owner_session_id: str
    through_message_id: str
    through_sequence: int
    summary_id: str | None
    intent_summary_content: str | None
    trajectory_summary_content: str | None
    covered_through_message_id: str | None
    covered_through_sequence: int | None
    system_prompt: str
    tools: tuple[str, ...]
    messages: tuple[Message, ...]

    def __post_init__(self) -> None:
        for field_name in (
            "triage_id",
            "project_owner_session_id",
            "through_message_id",
            "system_prompt",
        ):
            if not getattr(self, field_name).strip():
                raise ValueError(f"{field_name} must not be empty")
        if self.through_sequence <= 0:
            raise ValueError("through_sequence must be positive")
        if self.summary_id is None and any(
            value is not None
            for value in (
                self.covered_through_message_id,
                self.covered_through_sequence,
                self.intent_summary_content,
                self.trajectory_summary_content,
            )
        ):
            raise ValueError("Raw Owner context cannot contain a Summary watermark")
        if self.summary_id is not None and not self.summary_id.strip():
            raise ValueError("summary_id must not be empty")
        if self.summary_id is not None and (
            self.intent_summary_content is None
            or self.trajectory_summary_content is None
        ):
            raise ValueError("Summary-projected context must contain both summaries")
        if (self.covered_through_message_id is None) != (
            self.covered_through_sequence is None
        ):
            raise ValueError("Summary watermark ID and sequence must be present together")
        if self.covered_through_sequence is not None:
            if self.covered_through_sequence <= 0:
                raise ValueError("covered_through_sequence must be positive")
            if self.covered_through_sequence > self.through_sequence:
                raise ValueError("Summary watermark must not follow through_sequence")
        if not self.messages:
            raise ValueError("Restored Owner context must contain messages")
