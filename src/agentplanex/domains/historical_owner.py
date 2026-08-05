"""Read-only Historical Project Owner Fork results."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class HistoricalOwnerFidelity:
    """What one Historical Owner Fork can reproduce exactly or approximately."""

    message_checkpoint: str = "EXACT"
    summary_selection: str = "EXACT"
    agent_definition: str = "CURRENT_PERSISTED"
    model: str = "CURRENT_CONFIG_NEW_INVOCATION"


@dataclass(frozen=True, slots=True)
class HistoricalOwnerExchange:
    """One investigator question and Historical Owner witness answer."""

    turn: int
    question: str
    answer: str

    def __post_init__(self) -> None:
        if self.turn <= 0:
            raise ValueError("turn must be positive")
        if not self.question.strip():
            raise ValueError("question must not be empty")
        if not self.answer.strip():
            raise ValueError("answer must not be empty")
