"""Configured A2A Agent identities and stable Definitions."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from agentplanex.services.agent_invocation import AgentInvocationError
from agentplanex.settings import RuntimeSettings

_AGENT_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


class DelegatedAgentRole(StrEnum):
    """A2A role and its role-owned output semantics."""

    PLANNER = "planner"
    REVIEWER = "reviewer"
    TASK_DISTRIBUTOR = "task_distributor"


@dataclass(frozen=True, slots=True)
class AgentCard:
    """One Owner-visible Agent Card bound to a stable Definition."""

    agent_id: str
    name: str
    description: str
    role: DelegatedAgentRole


class AgentCatalog:
    """Validate and expose the three Owner-addressable external Agents."""

    __slots__ = ("cards",)

    cards: Mapping[str, AgentCard]

    def __init__(self, settings: RuntimeSettings) -> None:
        cards: dict[str, AgentCard] = {}
        for role in DelegatedAgentRole:
            agent_id = role.value
            configured = settings.external_agents.get(agent_id)
            if configured is None:
                raise AgentInvocationError(
                    f"Missing External Agent definition: {agent_id!r}"
                )
            if not _AGENT_ID.fullmatch(agent_id):
                raise AgentInvocationError(f"Invalid Agent ID: {agent_id!r}")
            cards[agent_id] = AgentCard(
                agent_id=agent_id,
                name=configured.name,
                description=configured.description,
                role=role,
            )
        self.cards = MappingProxyType(cards)

    def get(self, agent_id: str) -> AgentCard:
        try:
            return self.cards[agent_id]
        except KeyError as error:
            raise AgentInvocationError(f"Unknown Agent: {agent_id!r}") from error

    def describe(self) -> str:
        """Render configured Agent Cards for the model-visible Tool description."""
        return "\n".join(
            f"- {card.agent_id}: {card.name}. {card.description}"
            for card in self.cards.values()
        )
