"""Project Owner Agent message history domain object."""

from dataclasses import dataclass
from typing import Any

type Message = dict[str, Any]


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
