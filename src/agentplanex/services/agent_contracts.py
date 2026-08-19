"""Configured prompt catalog for AgentPlaneX invocations."""

from dataclasses import dataclass
from pathlib import Path

from agentplanex.agent_contracts import (
    InvocationContract,
    PromptRole,
    render_invocation,
)
from agentplanex.domains import AgentCollaborationError
from agentplanex.settings import (
    AgentPromptSettings,
    PromptSettings,
    TaskAgentPromptSettings,
)

OBSERVE_SKILL_NAME = "agentplanex-project-observe"
_PACKAGED_SKILL = (
    Path(__file__).resolve().parents[1]
    / "resources"
    / "skills"
    / OBSERVE_SKILL_NAME
    / "SKILL.md"
)


def resolve_observation_skill() -> Path:
    """Return the complete project-observation Skill shipped with AgentPlaneX."""

    detail = _PACKAGED_SKILL.parent / "references" / "detail.md"
    if _PACKAGED_SKILL.is_file() and detail.is_file():
        return _PACKAGED_SKILL
    raise AgentCollaborationError(
        f"Packaged {OBSERVE_SKILL_NAME} Skill is incomplete"
    )


@dataclass(frozen=True, slots=True)
class AgentPromptCatalog:
    """Compose configured Agent instructions with Runtime-owned invocation facts."""

    settings: PromptSettings

    def role_instructions(
        self,
        role: PromptRole,
        *,
        profile_instructions: str | None = None,
    ) -> str:
        """Return one role Contract with an optional Config-selected profile."""

        prompt = self._role(role).role.strip()
        profile = profile_instructions.strip() if profile_instructions is not None else ""
        return "\n\n".join(part for part in (prompt, profile) if part)

    def task_instructions(self, role: PromptRole) -> str:
        """Return the stable operation guidance configured for one role."""

        configured = self._role(role)
        if not isinstance(configured, TaskAgentPromptSettings):
            raise AgentCollaborationError(
                f"Prompt role has no task instructions: {role.value}"
            )
        return configured.task.strip()

    @property
    def summary_context_header(self) -> str:
        return self.settings.summary_context_header.strip()

    def render_invocation(self, contract: InvocationContract) -> str:
        """Render the small locator from which an Agent observes authoritative facts."""
        return render_invocation(
            contract,
            self.settings.observation_instruction,
        )

    def _role(self, role: PromptRole) -> AgentPromptSettings:
        return {
            PromptRole.PROJECT_OWNER: self.settings.project_owner,
            PromptRole.HISTORICAL_OWNER: self.settings.historical_owner,
            PromptRole.PLANNER: self.settings.planner,
            PromptRole.REVIEWER: self.settings.reviewer,
            PromptRole.PLAN_HARD_GATE: self.settings.plan_hard_gate,
            PromptRole.MILESTONE_HARD_GATE: self.settings.milestone_hard_gate,
            PromptRole.STAGE_EXECUTOR: self.settings.stage_executor,
        }[role]
