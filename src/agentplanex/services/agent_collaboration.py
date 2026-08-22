"""Blocking Config-driven Planner and Reviewer collaboration."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agentplanex.agent_contracts import InvocationContract, PromptRole
from agentplanex.domains.agent_collaboration import (
    AgentCard,
    AgentCollaborationError,
    AgentInteractionKind,
    AgentRole,
    ArtifactDescriptor,
    ResolvedArtifact,
    TalkToAgentRequest,
    TalkToAgentResult,
)
from agentplanex.domains.project_runtime_state import ProjectRuntimeState
from agentplanex.infrastructure.agent_workspace import (
    AgentInvocation,
    AgentWorkspaceStore,
)
from agentplanex.infrastructure.codex import (
    CodexTurnRequest,
    CodexTurnTransport,
)
from agentplanex.services.agent_contracts import (
    AgentPromptCatalog,
    resolve_observation_skill,
)
from agentplanex.settings import RuntimeSettings

_AGENT_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_SUMMARY_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
    "additionalProperties": False,
}


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


@dataclass(frozen=True, slots=True, init=False)
class AgentCatalog:
    """Validated Config-selected Agent Cards and Hard Gate binding."""

    cards: Mapping[str, AgentCard]
    plan_reviewer_id: str

    def __init__(self, settings: RuntimeSettings) -> None:
        cards: dict[str, AgentCard] = {}
        for agent_id, configured in settings.agents.items():
            normalized_id = agent_id.strip()
            if normalized_id != agent_id or not _AGENT_ID.fullmatch(agent_id):
                raise ValueError(f"Invalid Agent ID: {agent_id!r}")
            if any(
                not value.strip()
                for value in (
                    configured.name,
                    configured.description,
                )
            ):
                raise ValueError(f"Agent Card fields must not be blank: {agent_id!r}")
            role = AgentRole(configured.contract)
            digest_source = json.dumps(
                {
                    "agent_id": agent_id,
                    **configured.model_dump(mode="json"),
                },
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
            raise ValueError("At least one Config Agent Card is required")

        reviewer_id = settings.hard_gates.plan_approval.agent_id
        reviewer = cards.get(reviewer_id)
        if reviewer is None:
            raise ValueError(
                f"Plan Hard Gate references unknown Reviewer Agent: {reviewer_id!r}"
            )
        if reviewer.role is not AgentRole.REVIEWER:
            raise ValueError(
                f"Plan Hard Gate Agent must use the reviewer Contract: {reviewer_id!r}"
            )
        object.__setattr__(self, "cards", MappingProxyType(cards))
        object.__setattr__(self, "plan_reviewer_id", reviewer_id)

    def get(self, agent_id: str) -> AgentCard:
        try:
            return self.cards[agent_id]
        except KeyError as error:
            raise AgentCollaborationError(f"Unknown Agent: {agent_id!r}") from error

    def card_description(self) -> str:
        """Render Config Cards into a stable tool-schema description."""
        return "\n".join(
            f"- {card.agent_id} ({card.role.value}): {card.name}. {card.description}"
            for card in self.cards.values()
        )


@dataclass(frozen=True, slots=True)
class AgentCollaborationService:
    """Own synchronous Message/Task behavior without changing Runtime state."""

    catalog: AgentCatalog
    workspaces: AgentWorkspaceStore
    transport: CodexTurnTransport
    observation_skill: Path
    prompts: AgentPromptCatalog

    @classmethod
    def from_settings(
        cls,
        project_path: Path,
        settings: RuntimeSettings,
        *,
        observation_skill: Path | None = None,
    ) -> AgentCollaborationService:
        codex = settings.codex
        return cls(
            catalog=AgentCatalog(settings),
            workspaces=AgentWorkspaceStore(
                project_path=project_path,
                response_limit=codex.response_limit,
                artifact_limit=codex.artifact_limit,
            ),
            transport=CodexTurnTransport(
                executable=codex.executable,
                model=codex.model,
                timeout_seconds=codex.timeout_seconds,
                response_limit=codex.response_limit,
                network_access=codex.network_access,
            ),
            observation_skill=(
                observation_skill or resolve_observation_skill()
            ),
            prompts=AgentPromptCatalog(settings.prompts),
        )

    def talk(
        self,
        request: TalkToAgentRequest,
        context: ProjectRuntimeState,
    ) -> TalkToAgentResult:
        """Block until one configured Agent Message or Task has completed."""
        card = self.catalog.get(request.agent_id)
        message = request.message.strip()
        if not message:
            raise AgentCollaborationError("Agent message must not be empty")
        resolved = tuple(
            self.workspaces.resolve_artifact(artifact.uri)
            for artifact in request.artifacts
        )
        if request.conversation_id is None:
            workspace = self.workspaces.create(card)
            thread_id = None
        else:
            workspace, thread_id = self.workspaces.restore(
                card,
                request.conversation_id,
            )

        invocation = (
            self.workspaces.create_invocation(workspace)
            if request.kind is AgentInteractionKind.TASK
            else None
        )
        turn = self.transport.run(
            CodexTurnRequest(
                thread_id=thread_id,
                workspace=workspace.path,
                developer_instructions=self.prompts.role_instructions(
                    PromptRole(card.role.value),
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
                "plan.md" if card.role is AgentRole.PLANNER else "review.md"
            )
            artifact = manifest.artifacts[0]
            artifacts = (
                self.workspaces.output_artifact(
                    workspace,
                    artifact.path,
                    expected_name=expected_name,
                ),
            )
        return TalkToAgentResult(
            agent_id=card.agent_id,
            conversation_id=self.workspaces.encode_conversation(
                workspace,
                turn.thread_id,
            ),
            summary=summary,
            artifacts=artifacts,
        )

    def _task_manifest(self, invocation: AgentInvocation) -> _TaskResultManifest:
        try:
            return _TaskResultManifest.model_validate(
                self.workspaces.read_result_json(invocation)
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
            if card.role is AgentRole.PLANNER
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
        document_name = "plan.md" if card.role is AgentRole.PLANNER else "review.md"
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
                self.prompts.render_invocation(
                    InvocationContract(
                        role=PromptRole(card.role.value),
                        operation=f"{operation}:{kind.value}",
                        project_root=self.workspaces.project_path,
                        observation_skill=self.observation_skill,
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
                self.prompts.task_instructions(PromptRole(card.role.value)),
            )
        )
