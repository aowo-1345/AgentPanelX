"""Validated AgentPlaneX application settings."""

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

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
    profile_instructions: str | None = Field(default=None, min_length=1)
    contract: Literal["planner", "reviewer"]

    @field_validator("profile_instructions")
    @classmethod
    def _profile_not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Agent profile instructions must not be blank")
        return value


class AgentPromptSettings(_SettingsModel):
    """Stable human-authored instructions for one Agent role."""

    role: str = Field(min_length=1)

    @field_validator("role")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Prompt text must not be blank")
        return value


class TaskAgentPromptSettings(AgentPromptSettings):
    """Role instructions plus stable guidance for one operation family."""

    task: str = Field(min_length=1)

    @field_validator("task")
    @classmethod
    def _task_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Prompt text must not be blank")
        return value


class PromptSettings(_SettingsModel):
    """The complete configurable Prompt catalog for Runtime Agent invocations."""

    observation_instruction: str = Field(min_length=1)
    summary_context_header: str = Field(min_length=1)
    project_owner: AgentPromptSettings
    historical_owner: AgentPromptSettings
    planner: TaskAgentPromptSettings
    reviewer: TaskAgentPromptSettings
    plan_hard_gate: TaskAgentPromptSettings
    milestone_hard_gate: TaskAgentPromptSettings
    stage_executor: TaskAgentPromptSettings

    @field_validator("observation_instruction", "summary_context_header")
    @classmethod
    def _shared_text_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Prompt text must not be blank")
        return value


class PlanApprovalHardGateSettings(_SettingsModel):
    """Configured Reviewer used for the protected Plan approval action."""

    agent_id: str = Field(min_length=1)


class HardGateSettings(_SettingsModel):
    """Bindings for protected actions that require a configured Reviewer."""

    plan_approval: PlanApprovalHardGateSettings = PlanApprovalHardGateSettings(
        agent_id="reviewer"
    )


class RuntimeSettings(_SettingsModel):
    """Project Runtime tool settings."""

    bash: BashSettings = BashSettings()
    codex: CodexSettings = CodexSettings()
    agents: dict[str, AgentCardSettings] = Field(min_length=1)
    prompts: PromptSettings
    hard_gates: HardGateSettings = HardGateSettings()


class Settings(_SettingsModel):
    """Complete configuration passed into one Project Runtime."""

    project_owner_agent: ProjectOwnerAgentSettings
    runtime: RuntimeSettings


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
