"""Committed conversation facts and transient model presentation events."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentplanex.project_owner_agent.context.models import MessageHistory
    from agentplanex.services.project_runtime_context.models import OwnerActivation


@dataclass(frozen=True, slots=True)
class ConversationMessageAppended:
    """A committed message-history row for the Web Console projection."""

    triage_id: str
    history: MessageHistory
    activation: OwnerActivation | None = None


@dataclass(frozen=True, slots=True)
class ConversationActivationUpdated:
    """A committed Activation change that can alter a visible tool row."""

    triage_id: str
    activation: OwnerActivation


@dataclass(frozen=True, slots=True)
class ConversationResponseUpdated:
    """Transient model output for one Project Owner response turn."""

    triage_id: str
    activation_id: str
    response_id: str
    delta: str = ""
    failure: str | None = None
    call_id: str | None = None
    tool_name: str | None = None
    completed: bool = False


type ConversationEvent = (
    ConversationMessageAppended
    | ConversationActivationUpdated
    | ConversationResponseUpdated
)
