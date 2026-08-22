"""Configured model-visible contracts for Agent invocations."""

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any

from agentplanex.settings import (
    AgentPromptSettings,
    PromptSettings,
    RuntimeSettings,
    TaskAgentPromptSettings,
)

OBSERVE_SKILL_NAME = "agentplanex-project-observe"
_AGENT_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_PACKAGED_SKILL = (
    Path(__file__).resolve().parents[1]
    / "resources"
    / "skills"
    / OBSERVE_SKILL_NAME
    / "SKILL.md"
)


class InvocationRole(StrEnum):
    """Configured identity of one model-visible Agent invocation."""

    PROJECT_OWNER = "project_owner"
    HISTORICAL_OWNER = "historical_owner"
    PLANNER = "planner"
    REVIEWER = "reviewer"
    PLAN_HARD_GATE = "plan_hard_gate"
    MILESTONE_HARD_GATE = "milestone_hard_gate"
    STAGE_EXECUTOR = "stage_executor"


class DelegatedAgentRole(StrEnum):
    """Config-visible role of an Agent available for delegation."""

    PLANNER = "planner"
    REVIEWER = "reviewer"


class AgentInvocationError(ValueError):
    """Configured Agent identity or invocation policy is invalid."""


@dataclass(frozen=True, slots=True)
class AgentCard:
    """A configured Planner or Reviewer profile."""

    agent_id: str
    name: str
    description: str
    profile_instructions: str | None
    role: DelegatedAgentRole
    profile_digest: str


@dataclass(frozen=True, slots=True)
class InvocationContract:
    """Runtime facts that configuration must never interpolate or replace."""

    role: InvocationRole
    operation: str
    project_root: Path
    observation_skill: Path
    triage_id: str
    fixed_work_object: Mapping[str, object]
    workspace: Mapping[str, object]
    output_contract: Mapping[str, object]


def resolve_observation_skill() -> Path:
    """Return the complete project-observation Skill shipped with AgentPlaneX."""

    detail = _PACKAGED_SKILL.parent / "references" / "detail.md"
    if _PACKAGED_SKILL.is_file() and detail.is_file():
        return _PACKAGED_SKILL
    raise AgentInvocationError(
        f"Packaged {OBSERVE_SKILL_NAME} Skill is incomplete"
    )


@dataclass(frozen=True, slots=True)
class AgentPromptCatalog:
    """Compose configured instructions with Runtime-owned invocation facts."""

    settings: PromptSettings

    def role_instructions(
        self,
        role: InvocationRole,
        *,
        profile_instructions: str | None = None,
    ) -> str:
        """Return one role contract with an optional configured profile."""

        prompt = self._role(role).role.strip()
        profile = profile_instructions.strip() if profile_instructions is not None else ""
        return "\n\n".join(part for part in (prompt, profile) if part)

    def task_instructions(self, role: InvocationRole) -> str:
        """Return the stable operation guidance configured for one role."""

        configured = self._role(role)
        if not isinstance(configured, TaskAgentPromptSettings):
            raise AgentInvocationError(
                f"Prompt role has no task instructions: {role.value}"
            )
        return configured.task.strip()

    @property
    def summary_context_header(self) -> str:
        return self.settings.summary_context_header.strip()

    def render_invocation(self, contract: InvocationContract) -> str:
        """Render the locator from which an Agent observes authoritative facts."""

        envelope: dict[str, Any] = {
            "role": contract.role.value,
            "operation": contract.operation,
            "project_root": str(contract.project_root.resolve()),
            "observation_skill": str(contract.observation_skill),
            "triage_id": contract.triage_id,
            "fixed_work_object": dict(contract.fixed_work_object),
            "workspace": dict(contract.workspace),
            "output_contract": dict(contract.output_contract),
        }
        return "\n\n".join(
            (
                "AgentPlaneX invocation envelope (Runtime-provided identity):",
                json.dumps(envelope, ensure_ascii=False, indent=2),
                self.settings.observation_instruction.strip(),
            )
        )

    def _role(self, role: InvocationRole) -> AgentPromptSettings:
        return {
            InvocationRole.PROJECT_OWNER: self.settings.project_owner,
            InvocationRole.HISTORICAL_OWNER: self.settings.historical_owner,
            InvocationRole.PLANNER: self.settings.planner,
            InvocationRole.REVIEWER: self.settings.reviewer,
            InvocationRole.PLAN_HARD_GATE: self.settings.plan_hard_gate,
            InvocationRole.MILESTONE_HARD_GATE: self.settings.milestone_hard_gate,
            InvocationRole.STAGE_EXECUTOR: self.settings.stage_executor,
        }[role]


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
