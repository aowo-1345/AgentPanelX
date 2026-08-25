"""Static External Agent Contract for one AutoCodex takeover attempt."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
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


class AutoTakeoverPayload(BaseModel):
    """Small incremental input for one attempt in the persistent Feature Session."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    triage_id: str = Field(min_length=1)
    trigger_event_id: int = Field(gt=0)
    blocked_event: dict[str, object]
    attempt_id: str = Field(min_length=1)
    ordinal: Literal[1, 2]
    fence_token: str = Field(min_length=1)
    remaining_seconds: float = Field(gt=0)
    control_command_prefix: tuple[str, ...] = Field(min_length=1)
    owner_fork_command: tuple[str, ...] = Field(min_length=1)
    agentpanelx_source_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    target_feature_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    correction: str | None = None


class _AttributionDeclaration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: Literal["documents/attribution.md"]
    media_type: Literal["text/markdown"]


class _ResultManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1]
    decision: Literal["YES", "NO"]
    attribution: _AttributionDeclaration | None = None


class _SummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    summary: str = Field(min_length=1)


@dataclass(frozen=True, slots=True)
class AutoTakeoverOutput:
    decision: Literal["YES", "NO"]
    attribution: ArtifactDescriptor | None = None


@dataclass(frozen=True, slots=True)
class AutoTakeoverOperation:
    """Prepare one bounded activation and validate its durable result."""

    operation_key: str = "auto_takeover_v1"
    output_schema: ClassVar[dict[str, Any]] = _SUMMARY_SCHEMA

    def contract_fingerprint(self) -> object:
        return {
            "operation_key": self.operation_key,
            "manifest_version": 1,
            "decisions": ["YES", "NO"],
            "attribution_path": "documents/attribution.md",
        }

    @staticmethod
    def request_fingerprint(payload: AutoTakeoverPayload) -> object:
        return payload.model_dump(mode="json")

    def prepare(
        self,
        payload: AutoTakeoverPayload,
        context: AgentInvocationContext,
    ) -> PreparedAgentTurn:
        runtime_context: dict[str, object] = {
            "triage_id": payload.triage_id,
            "trigger_event_id": payload.trigger_event_id,
            "blocked_event": payload.blocked_event,
            "attempt": payload.ordinal,
            "remaining_seconds": payload.remaining_seconds,
            "control_command_prefix": payload.control_command_prefix,
            "owner_fork_command": payload.owner_fork_command,
        }
        if payload.correction is not None:
            runtime_context["runtime_correction"] = payload.correction
        contract = {
            "result_path": str(context.outbox_result_path.resolve()),
            "manifest_variants": {
                "YES": {
                    "version": 1,
                    "decision": "YES",
                    "attribution": None,
                },
                "NO": {
                    "version": 1,
                    "decision": "NO",
                    "attribution": {
                        "path": "documents/attribution.md",
                        "media_type": "text/markdown",
                    },
                },
            },
            "attribution_document_path": str(
                (context.invocation.workspace.path / "workspace/documents/attribution.md").resolve()
            ),
        }
        return PreparedAgentTurn(
            task_text=(
                "Fixed takeover task:\nInvestigate the newly persisted BLOCKED transition and "
                "either restore rolling delivery or prove that real user intervention is "
                "required."
            ),
            runtime_context_text=(
                "Current authoritative Runtime activation facts:\n"
                + json.dumps(runtime_context, ensure_ascii=False, indent=2, sort_keys=True)
            ),
            control_text=(
                "Activation output contract:\n"
                + json.dumps(contract, ensure_ascii=False, indent=2, sort_keys=True)
                + "\nWrite the exact result contract, then return only a JSON object containing "
                "one non-empty short summary field."
            ),
            execution_workspace=Path(context.workspaces.project_path),
        )

    def validate(
        self,
        payload: AutoTakeoverPayload,
        context: AgentInvocationContext,
        turn: CodexTurnResult,
    ) -> AutoTakeoverOutput:
        try:
            _SummaryResponse.model_validate_json(turn.final_response)
            manifest = _ResultManifest.model_validate(context.read_outbox_json())
        except ValidationError as error:
            raise ValueError("AutoTakeover result Contract is invalid") from error
        if manifest.decision == "YES":
            if manifest.attribution is not None:
                raise ValueError("AutoTakeover YES must not declare Attribution")
            return AutoTakeoverOutput(decision="YES")
        if manifest.attribution is None:
            raise ValueError("AutoTakeover NO requires Attribution")
        attribution_path = (
            context.invocation.workspace.path / "workspace" / manifest.attribution.path
        )
        proposal = attribution_path.read_text(encoding="utf-8")
        attribution_path.write_text(
            _source_commit_header(payload) + proposal,
            encoding="utf-8",
        )
        return AutoTakeoverOutput(
            decision="NO",
            attribution=context.publish_artifact(
                manifest.attribution.path,
                expected_name="attribution.md",
            ),
        )

    @staticmethod
    def dump_result(output: AutoTakeoverOutput) -> dict[str, Any]:
        return {
            "decision": output.decision,
            "attribution": (
                {
                    "uri": output.attribution.uri,
                    "project_relative_path": output.attribution.project_relative_path,
                    "media_type": output.attribution.media_type,
                    "size": output.attribution.size,
                    "sha256": output.attribution.sha256,
                }
                if output.attribution is not None
                else None
            ),
        }

    @staticmethod
    def load_result(
        payload: dict[str, Any],
        context: AgentInvocationContext,
    ) -> AutoTakeoverOutput:
        decision = payload.get("decision")
        if decision not in {"YES", "NO"}:
            raise ValueError("Stored AutoTakeover decision is invalid")
        raw_artifact = payload.get("attribution")
        if decision == "YES" and raw_artifact is not None:
            raise ValueError("Stored AutoTakeover YES is invalid")
        if decision == "NO" and not isinstance(raw_artifact, dict):
            raise ValueError("Stored AutoTakeover NO is missing Attribution")
        artifact = ArtifactDescriptor(**raw_artifact) if raw_artifact is not None else None
        if artifact is not None:
            context.workspaces.resolve_descriptor(artifact)
        return AutoTakeoverOutput(decision=decision, attribution=artifact)


def _source_commit_header(payload: AutoTakeoverPayload) -> str:
    return (
        "## Source Commits\n\n"
        f"- AgentPanelX Source Commit: `{payload.agentpanelx_source_commit}`\n"
        f"- Target Feature Commit: `{payload.target_feature_commit}`\n\n"
    )
