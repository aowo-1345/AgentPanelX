"""Validated AgentPlaneX application settings."""

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

DEFAULT_SETTINGS_PATH = Path("config/settings.yaml")
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_WORKSPACE_DATA_HOME = Path(".agentplanex")


class _SettingsModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ModelSettings(_SettingsModel):
    """Project Owner model connection settings."""

    adapter: Literal["qwen", "openai"]
    name: str = Field(min_length=1)
    base_url: str = Field(default=DEFAULT_OPENAI_BASE_URL, min_length=1)
    api_key_env: str = Field(default="OPENAI_API_KEY", min_length=1)
    http_headers: dict[str, str] = Field(default_factory=dict)
    reasoning_effort: Literal[
        "none", "minimal", "low", "medium", "high", "xhigh", "max"
    ] | None = None
    service_tier: Literal[
        "auto", "default", "flex", "scale", "priority"
    ] | None = "priority"
    timeout_seconds: float = Field(default=60.0, gt=0)

    @field_validator("name", "base_url", "api_key_env")
    @classmethod
    def _model_text_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Model configuration text must not be blank")
        return value


class ContextMemorySettings(_SettingsModel):
    """Project Owner query-time context compaction limits."""

    capacity_tokens: int = Field(default=128_000, gt=0)
    compaction_threshold: float = Field(default=0.8, gt=0, le=1)


class ProjectOwnerAgentSettings(_SettingsModel):
    """Long-lived Project Owner control-loop settings."""

    active_model: str = Field(min_length=1)
    models: dict[str, ModelSettings] = Field(min_length=1)
    step_limit: int = Field(default=20, gt=0)
    max_consecutive_format_errors: int = Field(default=3, gt=0)
    context_memory: ContextMemorySettings = Field(
        default_factory=ContextMemorySettings
    )

    @field_validator("active_model")
    @classmethod
    def _active_model_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Active model name must not be blank")
        return value

    @field_validator("models")
    @classmethod
    def _model_aliases_not_blank(
        cls, value: dict[str, ModelSettings]
    ) -> dict[str, ModelSettings]:
        if any(not alias.strip() for alias in value):
            raise ValueError("Model aliases must not be blank")
        return value

    @model_validator(mode="after")
    def _active_model_exists(self) -> "ProjectOwnerAgentSettings":
        if self.active_model not in self.models:
            raise ValueError(
                f"Active model {self.active_model!r} is not declared in models"
            )
        return self

    @property
    def selected_model(self) -> ModelSettings:
        """Return the explicitly selected Project Owner model provider."""

        return self.models[self.active_model]


class BashSettings(_SettingsModel):
    """Limits applied to project-scoped Bash executions."""

    timeout_seconds: float = Field(default=30.0, gt=0)
    output_limit: int = Field(default=10_000, gt=0)


class CodexSettings(_SettingsModel):
    """Limits and binary selection for one local Codex App Server invocation."""

    executable: str | None = None
    model: str | None = None
    network_access: bool = True
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
    trajectory_summary: str = Field(min_length=1)
    initial_intent_summary: str = Field(min_length=1)
    update_intent_summary: str = Field(min_length=1)
    project_owner: AgentPromptSettings
    historical_owner: AgentPromptSettings
    planner: TaskAgentPromptSettings
    reviewer: TaskAgentPromptSettings
    plan_hard_gate: TaskAgentPromptSettings
    milestone_hard_gate: TaskAgentPromptSettings
    stage_executor: TaskAgentPromptSettings

    @field_validator(
        "observation_instruction",
        "summary_context_header",
        "trajectory_summary",
        "initial_intent_summary",
        "update_intent_summary",
    )
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


class WorkspaceSettings(_SettingsModel):
    """User-level Registry and long-lived Feature worktree location."""

    data_home: Path = DEFAULT_WORKSPACE_DATA_HOME
    max_parallel_features: int = Field(default=4, ge=1)

    @field_validator("data_home")
    @classmethod
    def _expand_data_home(cls, value: Path) -> Path:
        return value.expanduser()


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
    workspace: WorkspaceSettings = WorkspaceSettings()


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
