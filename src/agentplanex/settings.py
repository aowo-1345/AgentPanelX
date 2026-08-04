"""Validated AgentPlaneX application settings."""

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

DEFAULT_SETTINGS_PATH = Path("config/settings.yaml")
DEFAULT_JBB_BASE_URL = "https://api.openai.com/v1"


class _SettingsModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ModelSettings(_SettingsModel):
    """Project Owner model connection settings."""

    name: str = Field(min_length=1)
    base_url: str = Field(default=DEFAULT_JBB_BASE_URL, min_length=1)
    timeout_seconds: float = Field(default=60.0, gt=0)


class ProjectOwnerAgentSettings(_SettingsModel):
    """Long-lived Project Owner control-loop settings."""

    model: ModelSettings
    step_limit: int = Field(default=20, gt=0)
    max_consecutive_format_errors: int = Field(default=3, gt=0)


class BashSettings(_SettingsModel):
    """Limits applied to project-scoped Bash executions."""

    timeout_seconds: float = Field(default=30.0, gt=0)
    output_limit: int = Field(default=65_536, gt=0)


class CodexSettings(_SettingsModel):
    """Limits and binary selection for one local Codex App Server invocation."""

    executable: str | None = None
    model: str | None = None
    timeout_seconds: float = Field(default=600.0, gt=0)
    response_limit: int = Field(default=65_536, gt=0)
    artifact_limit: int = Field(default=262_144, gt=0)


class AgentCardSettings(_SettingsModel):
    """One Config-declared local Planner or Reviewer profile."""

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    developer_instructions: str = Field(min_length=1)
    contract: Literal["planner", "reviewer"]


class PlanApprovalHardGateSettings(_SettingsModel):
    """Configured Reviewer used for the protected Plan approval action."""

    agent_id: str = Field(min_length=1)


class HardGateSettings(_SettingsModel):
    """Bindings for protected actions that require a configured Reviewer."""

    plan_approval: PlanApprovalHardGateSettings = PlanApprovalHardGateSettings(
        agent_id="reviewer"
    )


def _default_agent_cards() -> dict[str, AgentCardSettings]:
    return {
        "planner": AgentCardSettings(
            name="Planner",
            description="Create and refine Project Plans in a dedicated Agent workspace.",
            developer_instructions=(
                "Act as the Project Planner. Modify only your Agent workspace; "
                "never modify project source or Git refs."
            ),
            contract="planner",
        ),
        "reviewer": AgentCardSettings(
            name="Reviewer",
            description=(
                "Review Project Plans and fixed delivery Candidates in a dedicated "
                "Agent workspace."
            ),
            developer_instructions=(
                "Act as the Project Reviewer. Review Plans or Candidates and modify "
                "only your Agent workspace; never change project source or Git refs."
            ),
            contract="reviewer",
        ),
    }


class RuntimeSettings(_SettingsModel):
    """Project Runtime tool settings."""

    bash: BashSettings = BashSettings()
    codex: CodexSettings = CodexSettings()
    agents: dict[str, AgentCardSettings] = Field(default_factory=_default_agent_cards)
    hard_gates: HardGateSettings = HardGateSettings()


class Settings(_SettingsModel):
    """Complete configuration passed into one Project Runtime."""

    project_owner_agent: ProjectOwnerAgentSettings
    runtime: RuntimeSettings = RuntimeSettings()


def load_settings(path: Path | None = None) -> Settings:
    """Load settings using the configured path or the application default."""
    settings_path = path or Path(
        os.getenv("AGENTPLANEX_CONFIG", str(DEFAULT_SETTINGS_PATH))
    )
    try:
        raw = yaml.safe_load(settings_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise TypeError("settings root must be a mapping")
        return Settings.model_validate(raw)
    except (OSError, TypeError, ValidationError, yaml.YAMLError) as error:
        raise ValueError(
            f"Failed to load AgentPlaneX settings: {settings_path}"
        ) from error
