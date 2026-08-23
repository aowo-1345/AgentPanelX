"""Owner-visible A2A request and result values."""

from dataclasses import dataclass
from enum import StrEnum

from agentplanex.domains.artifact import ArtifactDescriptor


class AgentInteractionKind(StrEnum):
    """One synchronous delegated interaction shape."""

    MESSAGE = "message"
    TASK = "task"


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    """An opaque project-local or immutable Agent Artifact input."""

    uri: str


@dataclass(frozen=True, slots=True)
class TalkToAgentRequest:
    """Runtime-identified synchronous A2A activation."""

    request_key: str
    agent_id: str
    kind: AgentInteractionKind
    message: str
    artifacts: tuple[ArtifactRef, ...]


@dataclass(frozen=True, slots=True)
class TalkToAgentResult:
    """Bounded role result with immutable published Artifacts."""

    agent_id: str
    summary: str
    artifacts: tuple[ArtifactDescriptor, ...] = ()
