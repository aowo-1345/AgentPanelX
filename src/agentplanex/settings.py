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
    reasoning_effort: Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"] | None = (
        None
    )
    service_tier: Literal["auto", "default", "flex", "scale", "priority"] | None = "priority"
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
    context_memory: ContextMemorySettings = Field(default_factory=ContextMemorySettings)

    @field_validator("active_model")
    @classmethod
    def _active_model_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Active model name must not be blank")
        return value

    @field_validator("models")
    @classmethod
    def _model_aliases_not_blank(cls, value: dict[str, ModelSettings]) -> dict[str, ModelSettings]:
        if any(not alias.strip() for alias in value):
            raise ValueError("Model aliases must not be blank")
        return value

    @model_validator(mode="after")
    def _active_model_exists(self) -> "ProjectOwnerAgentSettings":
        if self.active_model not in self.models:
            raise ValueError(f"Active model {self.active_model!r} is not declared in models")
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


class AutoTakeoverSettings(_SettingsModel):
    """Ultra Mode policy for provisional BLOCKED transitions."""

    enabled: bool = False
    budget_seconds: float = Field(default=1800.0, gt=0, le=1800.0)


class ExternalAgentDefinitionSettings(_SettingsModel):
    """Stable configuration for one Owner-external Agent."""

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    instructions: str = Field(min_length=1)
    session_policy: Literal["feature", "activation", "stage_run"]
    skills: tuple[str, ...] = ()
    execution_policy: Literal[
        "agent_workspace",
        "candidate_worktree",
        "trusted_feature_user_proxy",
    ]
    allowed_operations: tuple[str, ...] = Field(min_length=1)

    @field_validator("name", "description", "instructions", "execution_policy")
    @classmethod
    def _external_text_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("External Agent configuration text must not be blank")
        return value

    @field_validator("skills", "allowed_operations")
    @classmethod
    def _external_items_not_blank(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() for item in value):
            raise ValueError("External Agent configuration items must not be blank")
        if len(set(value)) != len(value):
            raise ValueError("External Agent configuration items must be unique")
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


class PromptSettings(_SettingsModel):
    """The complete configurable Prompt catalog for Runtime Agent invocations."""

    observation_instruction: str = Field(min_length=1)
    summary_context_header: str = Field(min_length=1)
    trajectory_summary: str = Field(min_length=1)
    initial_intent_summary: str = Field(min_length=1)
    update_intent_summary: str = Field(min_length=1)
    project_owner: AgentPromptSettings
    historical_owner: AgentPromptSettings

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
    auto_takeover: AutoTakeoverSettings = AutoTakeoverSettings()
    external_agents: dict[str, ExternalAgentDefinitionSettings] = Field(min_length=1)
    prompts: PromptSettings

    @model_validator(mode="after")
    def _required_external_agents_exist(self) -> "RuntimeSettings":
        required = {
            "planner",
            "reviewer",
            "task_distributor",
            "plan_hard_gate",
            "milestone_hard_gate",
            "stage_executor",
        }
        if self.auto_takeover.enabled:
            required.add("auto_takeover")
        missing = sorted(required - self.external_agents.keys())
        if missing:
            raise ValueError("Missing required External Agent definitions: " + ", ".join(missing))
        return self


class Settings(_SettingsModel):
    """Complete configuration passed into one Project Runtime."""

    project_owner_agent: ProjectOwnerAgentSettings
    runtime: RuntimeSettings
    workspace: WorkspaceSettings = WorkspaceSettings()


def load_settings(path: Path | None = None) -> Settings:
    """Load settings using the configured path or the application default."""
    settings_path = resolve_settings_path(path)
    try:
        raw = yaml.safe_load(settings_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise TypeError("settings root must be a mapping")
        return Settings.model_validate(raw)
    except (OSError, TypeError, ValidationError, yaml.YAMLError) as error:
        raise ValueError(f"Failed to load AgentPlaneX settings: {settings_path}") from error


def resolve_settings_path(path: Path | None = None) -> Path:
    """Resolve the settings file once before child processes change directories."""
    configured = path or Path(os.getenv("AGENTPLANEX_CONFIG", str(DEFAULT_SETTINGS_PATH)))
    return configured.expanduser().resolve()
