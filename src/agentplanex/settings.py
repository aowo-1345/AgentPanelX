"""Validated AgentPlaneX application settings."""

import os
from pathlib import Path

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


class RuntimeSettings(_SettingsModel):
    """Project Runtime tool settings."""

    bash: BashSettings = BashSettings()


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
