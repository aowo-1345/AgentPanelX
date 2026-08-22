"""Blocking Config-driven Planner and Reviewer collaboration."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agentplanex.domains.artifact import ArtifactDescriptor
from agentplanex.domains.project_runtime_state import ProjectRuntimeState
from agentplanex.infrastructure.agent_workspace import (
    AgentInvocation,
    AgentWorkspaceError,
    AgentWorkspaceStore,
    ResolvedArtifact,
)
from agentplanex.infrastructure.codex import (
    CodexTransportError,
    CodexTurnRequest,
    CodexTurnTransport,
)
from agentplanex.services.agent_invocation import (
    AgentCard,
    AgentCatalog,
    AgentInvocationError,
    AgentPromptCatalog,
    DelegatedAgentRole,
    InvocationContract,
    InvocationRole,
)

_SUMMARY_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
    "additionalProperties": False,
}


class AgentInteractionKind(StrEnum):
    """One blocking delegated interaction shape."""

    MESSAGE = "message"
    TASK = "task"


class AgentCollaborationError(ValueError):
    """A delegated Agent request or result failed its business contract."""


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    """An opaque project-local or Agent-workspace input reference."""

    uri: str


@dataclass(frozen=True, slots=True)
class TalkToAgentRequest:
    """One model-visible synchronous delegated Agent request."""

    agent_id: str
    kind: AgentInteractionKind
    message: str
    conversation_id: str | None
    artifacts: tuple[ArtifactRef, ...]


@dataclass(frozen=True, slots=True)
class TalkToAgentResult:
    """Bounded result returned from one delegated Agent turn."""

    agent_id: str
    conversation_id: str
    summary: str
    artifacts: tuple[ArtifactDescriptor, ...] = ()


class _ManifestArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1)
    media_type: Literal["text/markdown"]


class _TaskResultManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1]
    summary: str = Field(min_length=1)
    artifacts: tuple[_ManifestArtifact, ...] = Field(min_length=1, max_length=1)


class _SummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    summary: str = Field(min_length=1)


class AgentCollaborationService:
    """Own synchronous Message/Task behavior without changing Runtime state."""

    __slots__ = (
        "_catalog",
        "_observation_skill",
        "_prompts",
        "_transport",
        "_workspaces",
    )

    _catalog: AgentCatalog
    _workspaces: AgentWorkspaceStore
    _transport: CodexTurnTransport
    _observation_skill: Path
    _prompts: AgentPromptCatalog

    def __init__(
        self,
        *,
        catalog: AgentCatalog,
        workspaces: AgentWorkspaceStore,
        transport: CodexTurnTransport,
        observation_skill: Path,
        prompts: AgentPromptCatalog,
    ) -> None:
        self._catalog = catalog
        self._workspaces = workspaces
        self._transport = transport
        self._observation_skill = observation_skill
        self._prompts = prompts

    def describe_agents(self) -> str:
        """Render configured Agent Cards for the Tool description."""

        return self._catalog.describe()

    def require_agent(self, agent_id: str) -> None:
        """Validate an Agent before recording an invocation attempt."""

        try:
            self._catalog.get(agent_id)
        except AgentInvocationError as error:
            raise AgentCollaborationError(str(error)) from error

    def talk(
        self,
        request: TalkToAgentRequest,
        context: ProjectRuntimeState,
    ) -> TalkToAgentResult:
        """Block until one configured Agent Message or Task has completed."""

        try:
            return self._talk(request, context)
        except AgentCollaborationError:
            raise
        except (
            AgentInvocationError,
            AgentWorkspaceError,
            CodexTransportError,
        ) as error:
            raise AgentCollaborationError(str(error)) from error

    def _talk(
        self,
        request: TalkToAgentRequest,
        context: ProjectRuntimeState,
    ) -> TalkToAgentResult:
        card = self._catalog.get(request.agent_id)
        message = request.message.strip()
        if not message:
            raise AgentCollaborationError("Agent message must not be empty")
        resolved = tuple(
            self._workspaces.resolve_artifact(artifact.uri)
            for artifact in request.artifacts
        )
        if request.conversation_id is None:
            workspace = self._workspaces.create(
                agent_id=card.agent_id,
                profile_digest=card.profile_digest,
            )
            thread_id = None
        else:
            workspace, thread_id = self._workspaces.restore(
                agent_id=card.agent_id,
                profile_digest=card.profile_digest,
                conversation_id=request.conversation_id,
            )

        invocation = (
            self._workspaces.create_invocation(workspace)
            if request.kind is AgentInteractionKind.TASK
            else None
        )
        turn = self._transport.run(
            CodexTurnRequest(
                thread_id=thread_id,
                workspace=workspace.path,
                developer_instructions=self._prompts.role_instructions(
                    InvocationRole(card.role.value),
                    profile_instructions=card.profile_instructions,
                ),
                message=self._prompt(
                    card,
                    request.kind,
                    message,
                    resolved,
                    invocation,
                    context,
                ),
                mentions=tuple(
                    (f"artifact-{index + 1}-{artifact.path.name}", artifact.path)
                    for index, artifact in enumerate(resolved)
                ),
                output_schema=_SUMMARY_OUTPUT_SCHEMA,
            )
        )
        summary = self._summary_from_final_response(turn.final_response)
        artifacts: tuple[ArtifactDescriptor, ...] = ()
        if invocation is not None:
            manifest = self._task_manifest(invocation)
            summary = self._bounded_summary(manifest.summary)
            expected_name = (
                "plan.md"
                if card.role is DelegatedAgentRole.PLANNER
                else "review.md"
            )
            artifact = manifest.artifacts[0]
            artifacts = (
                self._workspaces.output_artifact(
                    workspace,
                    artifact.path,
                    expected_name=expected_name,
                ),
            )
        return TalkToAgentResult(
            agent_id=card.agent_id,
            conversation_id=self._workspaces.encode_conversation(
                workspace,
                turn.thread_id,
            ),
            summary=summary,
            artifacts=artifacts,
        )

    def _task_manifest(self, invocation: AgentInvocation) -> _TaskResultManifest:
        try:
            return _TaskResultManifest.model_validate(
                self._workspaces.read_result_json(invocation)
            )
        except ValidationError as error:
            raise AgentCollaborationError(
                "Agent result.json does not match the role Task Contract"
            ) from error

    @staticmethod
    def _summary_from_final_response(final_response: str) -> str:
        try:
            response = _SummaryResponse.model_validate_json(final_response)
        except ValidationError as error:
            raise AgentCollaborationError(
                "Agent final response does not contain a valid summary"
            ) from error
        return AgentCollaborationService._bounded_summary(response.summary)

    @staticmethod
    def _bounded_summary(summary: str) -> str:
        normalized = " ".join(summary.split())
        if not normalized:
            raise AgentCollaborationError("Agent summary must not be empty")
        return normalized[:2_000]

    def _prompt(
        self,
        card: AgentCard,
        kind: AgentInteractionKind,
        message: str,
        artifacts: tuple[ResolvedArtifact, ...],
        invocation: AgentInvocation | None,
        context: ProjectRuntimeState,
    ) -> str:
        operation = (
            "project_planning"
            if card.role is DelegatedAgentRole.PLANNER
            else "delegated_review"
        )
        fixed_work_object = {
            "delegated_request_sha256": hashlib.sha256(
                message.encode("utf-8")
            ).hexdigest(),
            "input_artifacts": [
                {"uri": artifact.uri, "sha256": artifact.sha256}
                for artifact in artifacts
            ],
            "runtime_anchor": {
                "status": context.status,
                "pending_action": context.pending_action,
                "plan_commit_sha": context.current_plan_commit_sha,
                "pending_plan_subject_digest": (
                    context.pending_plan_subject_digest
                ),
                "snapshot_id": context.current_snapshot_id,
                "run_id": context.current_run_id,
                "milestone_key": context.current_milestone_key,
                "stage_key": context.current_stage_key,
                "candidate_commit_sha": context.current_candidate_commit_sha,
            },
        }
        document_name = (
            "plan.md" if card.role is DelegatedAgentRole.PLANNER else "review.md"
        )
        output_contract: dict[str, object] = {
            "interaction": kind.value,
            "final_response": {"format": "json", "required_fields": ["summary"]},
            "outbox": None,
        }
        if kind is AgentInteractionKind.TASK:
            if invocation is None:
                raise AssertionError("Task interaction has no Outbox invocation")
            output_contract["outbox"] = {
                "result_path": str(invocation.result_path),
                "manifest_schema": _TaskResultManifest.model_json_schema(),
                "artifact_contract": {
                    "path": f"documents/{document_name}",
                    "media_type": "text/markdown",
                },
            }
        return "\n\n".join(
            (
                message,
                self._prompts.render_invocation(
                    InvocationContract(
                        role=InvocationRole(card.role.value),
                        operation=f"{operation}:{kind.value}",
                        project_root=self._workspaces.project_path,
                        observation_skill=self._observation_skill,
                        triage_id=context.triage_id,
                        fixed_work_object=fixed_work_object,
                        workspace={
                            "write_scope": "current_agent_workspace",
                            "project_and_runtime": "read_only",
                            "attached_artifacts": "read_only",
                        },
                        output_contract=output_contract,
                    )
                ),
                self._prompts.task_instructions(InvocationRole(card.role.value)),
            )
        )
