"""Public value objects for invoking Owner-external Agents."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel

from agentplanex.infrastructure.agent_workspace import ResolvedArtifact


class SessionPolicy(StrEnum):
    """How Runtime selects the Codex transcript for an invocation."""

    FEATURE = "feature"
    ACTIVATION = "activation"
    STAGE_RUN = "stage_run"


class ExecutionPolicy(StrEnum):
    """Where the Codex turn may write."""

    AGENT_WORKSPACE = "agent_workspace"
    CANDIDATE_WORKTREE = "candidate_worktree"
    TRUSTED_FEATURE_USER_PROXY = "trusted_feature_user_proxy"


@dataclass(frozen=True, slots=True)
class AgentSkill:
    """One stable native Codex Skill binding."""

    name: str
    path: Path


@dataclass(frozen=True, slots=True)
class AgentDefinition:
    """Stable identity, context policy, and permissions for one Agent."""

    agent_key: str
    stable_instructions: str
    session_policy: SessionPolicy
    bound_skills: tuple[AgentSkill, ...]
    execution_policy: ExecutionPolicy
    allowed_operation_keys: tuple[str, ...]
    protocol_digest: str


@dataclass(frozen=True, slots=True)
class ManagedAgentScope:
    """Runtime-authenticated business identity used to select a Session."""

    triage_id: str
    stage_run_id: str | None = None


@dataclass(frozen=True, slots=True)
class ExternalAgentRequest[InputT: BaseModel]:
    """Incremental activation input accepted by the shared Runtime."""

    agent_key: str
    operation_key: str
    request_key: str
    scope: ManagedAgentScope
    payload: InputT


@dataclass(frozen=True, slots=True)
class PreparedAgentTurn:
    """One Operation's model-visible incremental input."""

    task_text: str
    runtime_context_text: str = ""
    resources: tuple[ResolvedArtifact, ...] = ()
    control_text: str = ""
    execution_workspace: Path | None = None


@dataclass(frozen=True, slots=True)
class ExternalAgentResult[OutputT]:
    """A statically validated result, new or replayed."""

    request_key: str
    output: OutputT
    replayed: bool = field(default=False, compare=False)
