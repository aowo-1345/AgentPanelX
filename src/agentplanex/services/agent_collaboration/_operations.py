"""A2A role Operations executed through ExternalAgentRuntime."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agentplanex.domains.artifact import ArtifactDescriptor
from agentplanex.infrastructure.agent_workspace import (
    AgentWorkspaceStore,
    ResolvedArtifact,
)
from agentplanex.infrastructure.codex import CodexTurnResult
from agentplanex.services.agent_collaboration.models import AgentInteractionKind
from agentplanex.services.external_agent_runtime import (
    AgentInvocationContext,
    PreparedAgentTurn,
)

_SUMMARY_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
    "additionalProperties": False,
}


class A2APayload(BaseModel):
    """Role-specific activation data assembled by Runtime, not the Owner model."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: AgentInteractionKind
    message: str
    triage_id: str
    status: str
    pending_action: str | None
    plan_commit_sha: str | None
    snapshot_id: str | None
    run_id: str | None
    milestone_key: str | None
    stage_key: str | None
    candidate_commit_sha: str | None
    artifact_uris: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class A2AOperationOutput:
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


@dataclass(frozen=True, slots=True)
class A2AOperation:
    """One role and interaction-specific static Contract."""

    operation_key: str
    document_name: str | None
    workspaces: AgentWorkspaceStore
    output_schema: ClassVar[dict[str, Any]] = _SUMMARY_OUTPUT_SCHEMA

    def contract_fingerprint(self) -> object:
        return {
            "operation_key": self.operation_key,
            "document_name": self.document_name,
        }

    def request_fingerprint(self, payload: A2APayload) -> object:
        resolved = tuple(self.workspaces.resolve_artifact(uri) for uri in payload.artifact_uris)
        return {
            "payload": payload.model_dump(mode="json"),
            "attachments": [
                {
                    "uri": artifact.uri,
                    "size": artifact.size,
                    "sha256": artifact.sha256,
                }
                for artifact in resolved
            ],
        }

    def prepare(
        self,
        payload: A2APayload,
        context: AgentInvocationContext,
    ) -> PreparedAgentTurn:
        resources = tuple(self.workspaces.resolve_artifact(uri) for uri in payload.artifact_uris)
        staged: list[ResolvedArtifact] = []
        for index, resource in enumerate(resources):
            content = resource.path.read_bytes()
            if hashlib.sha256(content).hexdigest() != resource.sha256:
                raise ValueError("A2A attachment changed while it was being staged")
            fixed = context.stage_input(
                f"attachment-{index + 1}-{resource.path.name}",
                content,
                media_type=resource.media_type,
            )
            staged.append(
                ResolvedArtifact(
                    uri=resource.uri,
                    path=fixed.path,
                    media_type=resource.media_type,
                    size=resource.size,
                    sha256=resource.sha256,
                )
            )
        runtime_context = {
            "triage_id": payload.triage_id,
            "status": payload.status,
            "pending_action": payload.pending_action,
            "plan_commit_sha": payload.plan_commit_sha,
            "snapshot_id": payload.snapshot_id,
            "run_id": payload.run_id,
            "milestone_key": payload.milestone_key,
            "stage_key": payload.stage_key,
            "candidate_commit_sha": payload.candidate_commit_sha,
            "attachments": [
                {"uri": resource.uri, "sha256": resource.sha256} for resource in resources
            ],
        }
        control = "Return only a JSON object containing one short summary field."
        if payload.kind is AgentInteractionKind.TASK:
            if self.document_name is None:
                raise ValueError("Task Operation has no document Contract")
            control = (
                f"Write the role document to documents/{self.document_name} and the "
                f"stable Task manifest to {context.outbox_result_path}. " + control
            )
        return PreparedAgentTurn(
            task_text=payload.message,
            runtime_context_text=(
                "Current AgentPlaneX Runtime context:\n"
                + json.dumps(runtime_context, ensure_ascii=False, sort_keys=True)
            ),
            resources=tuple(staged),
            control_text=control,
        )

    def validate(
        self,
        payload: A2APayload,
        context: AgentInvocationContext,
        turn: CodexTurnResult,
    ) -> A2AOperationOutput:
        try:
            response = _SummaryResponse.model_validate_json(turn.final_response)
        except ValidationError as error:
            raise ValueError("Agent final response does not contain a valid summary") from error
        summary = self._bounded_summary(response.summary)
        if payload.kind is AgentInteractionKind.MESSAGE:
            return A2AOperationOutput(summary=summary)
        if self.document_name is None:
            raise ValueError("Task Operation has no document Contract")
        try:
            manifest = _TaskResultManifest.model_validate(context.read_outbox_json())
        except ValidationError as error:
            raise ValueError("Agent result.json does not match the role Task Contract") from error
        artifact = manifest.artifacts[0]
        if artifact.path != f"documents/{self.document_name}":
            raise ValueError(f"Agent Contract requires documents/{self.document_name}")
        published = context.publish_artifact(
            artifact.path,
            expected_name=self.document_name,
        )
        return A2AOperationOutput(
            summary=self._bounded_summary(manifest.summary),
            artifacts=(published,),
        )

    def dump_result(self, output: A2AOperationOutput) -> dict[str, Any]:
        return {
            "summary": output.summary,
            "artifacts": [
                {
                    "uri": artifact.uri,
                    "project_relative_path": artifact.project_relative_path,
                    "media_type": artifact.media_type,
                    "size": artifact.size,
                    "sha256": artifact.sha256,
                }
                for artifact in output.artifacts
            ],
        }

    def load_result(
        self,
        payload: dict[str, Any],
        context: AgentInvocationContext,
    ) -> A2AOperationOutput:
        summary = payload.get("summary")
        artifacts = payload.get("artifacts")
        if not isinstance(summary, str) or not isinstance(artifacts, list):
            raise ValueError("Stored A2A result is invalid")
        try:
            loaded = tuple(ArtifactDescriptor(**artifact) for artifact in artifacts)
        except (TypeError, ValueError) as error:
            raise ValueError("Stored A2A Artifact is invalid") from error
        for artifact in loaded:
            context.workspaces.resolve_descriptor(artifact)
        return A2AOperationOutput(
            summary=self._bounded_summary(summary),
            artifacts=loaded,
        )

    @staticmethod
    def _bounded_summary(summary: str) -> str:
        normalized = " ".join(summary.split())
        if not normalized:
            raise ValueError("Agent summary must not be empty")
        return normalized[:2_000]
