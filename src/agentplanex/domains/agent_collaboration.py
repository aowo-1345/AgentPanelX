"""Typed contracts for local Planner and Reviewer collaboration."""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class AgentRole(StrEnum):
    """The two Config-visible Agent contracts."""

    PLANNER = "planner"
    REVIEWER = "reviewer"


class AgentInteractionKind(StrEnum):
    """One blocking A2A interaction shape."""

    MESSAGE = "message"
    TASK = "task"


class AgentCollaborationError(ValueError):
    """An expected collaboration request, transport, or Contract failure."""


@dataclass(frozen=True, slots=True)
class AgentCard:
    """A Config-selected Planner or Reviewer profile."""

    agent_id: str
    name: str
    description: str
    profile_instructions: str | None
    role: AgentRole
    profile_digest: str


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    """An opaque project-local or Agent-workspace file reference."""

    uri: str


@dataclass(frozen=True, slots=True)
class ResolvedArtifact:
    """A validated Artifact path and the facts observed by Runtime."""

    uri: str
    path: Path
    media_type: str
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class ArtifactDescriptor:
    """A validated Agent output exposed to the Project Owner."""

    uri: str
    project_relative_path: str
    media_type: str
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class ConversationReference:
    """Runtime-scoped identity of one Codex thread and writable workspace."""

    agent_id: str
    profile_digest: str
    workspace_id: str
    thread_id: str


@dataclass(frozen=True, slots=True)
class TalkToAgentRequest:
    """One model-visible, synchronous Planner or Reviewer request."""

    agent_id: str
    kind: AgentInteractionKind
    message: str
    conversation_id: str | None
    artifacts: tuple[ArtifactRef, ...]


@dataclass(frozen=True, slots=True)
class TalkToAgentResult:
    """Bounded result returned from a blocking local Agent turn."""

    agent_id: str
    conversation_id: str
    summary: str
    artifacts: tuple[ArtifactDescriptor, ...] = ()
