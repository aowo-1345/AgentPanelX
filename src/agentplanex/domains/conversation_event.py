"""Committed Project Owner conversation facts."""

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


type ConversationEvent = ConversationMessageAppended | ConversationActivationUpdated
