"""Public delegated Agent collaboration service."""

from agentplanex.services.agent_collaboration._service import (
    AgentCollaborationError,
    AgentCollaborationService,
    AgentInteractionKind,
    ArtifactRef,
    TalkToAgentRequest,
    TalkToAgentResult,
)

__all__ = [
    "AgentCollaborationError",
    "AgentCollaborationService",
    "AgentInteractionKind",
    "ArtifactRef",
    "TalkToAgentRequest",
    "TalkToAgentResult",
]
