"""Configured Planner and Reviewer identities owned by Agent Collaboration."""

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from agentplanex.services.agent_invocation import AgentInvocationError
from agentplanex.settings import RuntimeSettings

_AGENT_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


class DelegatedAgentRole(StrEnum):
    """Config-visible role of an Agent available for delegation."""

    PLANNER = "planner"
    REVIEWER = "reviewer"


@dataclass(frozen=True, slots=True)
class AgentCard:
    """A configured Planner or Reviewer profile."""

    agent_id: str
    name: str
    description: str
    profile_instructions: str | None
    role: DelegatedAgentRole
    profile_digest: str


class AgentCatalog:
    """Validate configured delegated Agents and the Hard Gate Reviewer binding."""

    __slots__ = ("cards", "hard_gate_reviewer_id")

    cards: Mapping[str, AgentCard]
    hard_gate_reviewer_id: str

    def __init__(self, settings: RuntimeSettings) -> None:
        cards: dict[str, AgentCard] = {}
        for agent_id, configured in settings.agents.items():
            normalized_id = agent_id.strip()
            if normalized_id != agent_id or not _AGENT_ID.fullmatch(agent_id):
                raise AgentInvocationError(f"Invalid Agent ID: {agent_id!r}")
            if any(
                not value.strip()
                for value in (configured.name, configured.description)
            ):
                raise AgentInvocationError(
                    f"Agent Card fields must not be blank: {agent_id!r}"
                )
            role = DelegatedAgentRole(configured.contract)
            digest_source = json.dumps(
                {"agent_id": agent_id, **configured.model_dump(mode="json")},
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            cards[agent_id] = AgentCard(
                agent_id=agent_id,
                name=configured.name,
                description=configured.description,
                profile_instructions=configured.profile_instructions,
                role=role,
                profile_digest=hashlib.sha256(digest_source).hexdigest(),
            )
        if not cards:
            raise AgentInvocationError("At least one Config Agent Card is required")

        reviewer_id = settings.hard_gates.plan_approval.agent_id
        reviewer = cards.get(reviewer_id)
        if reviewer is None:
            raise AgentInvocationError(
                f"Plan Hard Gate references unknown Reviewer Agent: {reviewer_id!r}"
            )
        if reviewer.role is not DelegatedAgentRole.REVIEWER:
            raise AgentInvocationError(
                f"Plan Hard Gate Agent must use the reviewer Contract: {reviewer_id!r}"
            )
        self.cards = MappingProxyType(cards)
        self.hard_gate_reviewer_id = reviewer_id

    def get(self, agent_id: str) -> AgentCard:
        try:
            return self.cards[agent_id]
        except KeyError as error:
            raise AgentInvocationError(f"Unknown Agent: {agent_id!r}") from error

    def describe(self) -> str:
        """Render configured Agent Cards for the model-visible Tool description."""

        return "\n".join(
            f"- {card.agent_id} ({card.role.value}): {card.name}. {card.description}"
            for card in self.cards.values()
        )
