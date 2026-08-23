"""Configured model-visible contracts for Agent invocations."""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from agentplanex.settings import (
    AgentPromptSettings,
    PromptSettings,
)

OBSERVE_SKILL_NAME = "agentplanex-project-observe"
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


class AgentInvocationError(ValueError):
    """Configured Agent identity or invocation policy is invalid."""


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
    ) -> str:
        """Return one stable Owner role contract."""

        return self._role(role).role.strip()

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
        }[role]
