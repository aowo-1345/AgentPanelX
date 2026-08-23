"""Shared static Contract mechanics for the two business-owned Hard Gates."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agentplanex.domains.artifact import ArtifactDescriptor
from agentplanex.infrastructure.codex import CodexTurnResult
from agentplanex.services.external_agent_runtime import (
    AgentInvocationContext,
    PreparedAgentTurn,
)

_SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
    "additionalProperties": False,
}


class GateResource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    content_base64: str


class HardGatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    triage_id: str
    subject_name: str
    subject_digest: str
    task: str
    runtime_context: dict[str, str | None]
    resources: tuple[GateResource, ...]


class _Artifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    media_type: Literal["text/markdown"]


class _Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1]
    subject_digest: str
    decision: Literal["pass", "revise"]
    summary: str
    required_changes: tuple[str, ...]
    artifacts: tuple[_Artifact, ...] = Field(min_length=1, max_length=1)


@dataclass(frozen=True, slots=True)
class HardGateOutput:
    subject_digest: str
    decision: Literal["pass", "revise"]
    summary: str
    required_changes: tuple[str, ...]
    audit_artifact: ArtifactDescriptor


@dataclass(frozen=True, slots=True)
class HardGateOperation:
    operation_key: str
    output_schema: ClassVar[dict[str, Any]] = _SUMMARY_SCHEMA

    def contract_fingerprint(self) -> object:
        return {"operation_key": self.operation_key, "document_name": "review.md"}

    @staticmethod
    def request_fingerprint(payload: HardGatePayload) -> object:
        return payload.model_dump(mode="json")

    def prepare(
        self,
        payload: HardGatePayload,
        context: AgentInvocationContext,
    ) -> PreparedAgentTurn:
        resolved = []
        for resource in payload.resources:
            try:
                content = base64.b64decode(resource.content_base64, validate=True)
            except ValueError as error:
                raise ValueError("Hard Gate resource is not valid base64") from error
            resolved.append(
                context.stage_input(
                    resource.name,
                    content,
                    media_type=(
                        "text/markdown"
                        if resource.name.lower().endswith(".md")
                        else "application/json"
                    ),
                )
            )
        control = {
            "result_path": str(context.outbox_result_path),
            "subject_digest": payload.subject_digest,
            "review_document": "documents/review.md",
            "rules": {
                "pass_required_changes": [],
                "revise_requires_concrete_changes": True,
            },
        }
        return PreparedAgentTurn(
            task_text=payload.task,
            runtime_context_text=(
                "Fixed Hard Gate subject:\n"
                + json.dumps(
                    {
                        "triage_id": payload.triage_id,
                        "subject_name": payload.subject_name,
                        "subject_digest": payload.subject_digest,
                        **payload.runtime_context,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            ),
            resources=tuple(resolved),
            control_text=(
                "Write the exact Contract outputs described here and return only a short "
                f"JSON summary:\n{json.dumps(control, ensure_ascii=False, sort_keys=True)}"
            ),
        )

    def validate(
        self,
        payload: HardGatePayload,
        context: AgentInvocationContext,
        _turn: CodexTurnResult,
    ) -> HardGateOutput:
        try:
            manifest = _Manifest.model_validate(context.read_outbox_json())
        except ValidationError as error:
            raise ValueError("Hard Gate manifest is invalid") from error
        summary = " ".join(manifest.summary.split())
        changes = tuple(
            normalized
            for change in manifest.required_changes
            if (normalized := " ".join(change.split()))
        )
        if manifest.subject_digest != payload.subject_digest:
            raise ValueError("Hard Gate result identifies a different subject")
        if not summary:
            raise ValueError("Hard Gate summary is empty")
        if manifest.decision == "pass" and changes:
            raise ValueError("Hard Gate pass must not contain required changes")
        if manifest.decision == "revise" and not changes:
            raise ValueError("Hard Gate revise requires concrete changes")
        artifact = manifest.artifacts[0]
        if artifact.path != "documents/review.md":
            raise ValueError("Hard Gate requires documents/review.md")
        return HardGateOutput(
            subject_digest=manifest.subject_digest,
            decision=manifest.decision,
            summary=summary,
            required_changes=changes,
            audit_artifact=context.publish_artifact(
                artifact.path,
                expected_name="review.md",
            ),
        )

    @staticmethod
    def dump_result(output: HardGateOutput) -> dict[str, Any]:
        artifact = output.audit_artifact
        return {
            "subject_digest": output.subject_digest,
            "decision": output.decision,
            "summary": output.summary,
            "required_changes": list(output.required_changes),
            "audit_artifact": {
                "uri": artifact.uri,
                "project_relative_path": artifact.project_relative_path,
                "media_type": artifact.media_type,
                "size": artifact.size,
                "sha256": artifact.sha256,
            },
        }

    def load_result(
        self,
        payload: dict[str, Any],
        context: AgentInvocationContext,
    ) -> HardGateOutput:
        try:
            artifact = ArtifactDescriptor(**payload["audit_artifact"])
            decision = payload["decision"]
            if decision not in {"pass", "revise"}:
                raise ValueError("invalid decision")
            output = HardGateOutput(
                subject_digest=str(payload["subject_digest"]),
                decision=decision,
                summary=str(payload["summary"]),
                required_changes=tuple(str(item) for item in payload["required_changes"]),
                audit_artifact=artifact,
            )
            context.workspaces.resolve_descriptor(output.audit_artifact)
            return output
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("Stored Hard Gate result is invalid") from error
