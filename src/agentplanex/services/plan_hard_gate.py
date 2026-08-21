"""Real Codex-backed Plan Hard Gate using a configured generic Reviewer."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agentplanex.agent_contracts import InvocationContract, PromptRole
from agentplanex.domains import AgentCollaborationError, AgentRole, ArtifactDescriptor
from agentplanex.infrastructure.codex import CodexTurnRequest
from agentplanex.services.agent_collaboration import AgentCollaborationService
from agentplanex.services.delivery.contracts import (
    DeliveryError,
    MilestoneReviewRequest,
    MilestoneReviewResult,
)
from agentplanex.services.planning.contracts import (
    PlanningError,
    PlanReviewRequest,
    PlanReviewResult,
)

_SUMMARY_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
    "additionalProperties": False,
}


class _HardGateArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1)
    media_type: Literal["text/markdown"]


class _HardGateManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1]
    subject_digest: str = Field(min_length=1)
    decision: Literal["pass", "revise"]
    summary: str = Field(min_length=1)
    required_changes: tuple[str, ...]
    artifacts: tuple[_HardGateArtifact, ...] = Field(min_length=1, max_length=1)


@dataclass(frozen=True, slots=True)
class _HardGateReview:
    subject_digest: str
    decision: Literal["pass", "revise"]
    summary: str
    required_changes: tuple[str, ...]
    audit_artifact: ArtifactDescriptor


@dataclass(frozen=True, slots=True)
class CodexPlanHardGate:
    """Own the protected Plan decision Contract over one configured Reviewer."""

    collaboration: AgentCollaborationService

    def review(self, request: PlanReviewRequest) -> PlanReviewResult:
        """Run and validate one isolated Reviewer workspace fail closed."""
        subject = request.subject

        def mentions(workspace: Path) -> tuple[tuple[str, Path], ...]:
            inputs = workspace / "inputs"
            inputs.mkdir(parents=True, exist_ok=True)
            materialized: list[tuple[str, Path]] = []
            for index, document in enumerate(subject.documents):
                path = inputs / document.name
                path.write_bytes(document.content)
                materialized.append((f"plan-{index + 1}-{document.name}", path))
            return tuple(materialized)

        review = self._review_exact_subject(
            triage_id=request.triage_id,
            role=PromptRole.PLAN_HARD_GATE,
            subject_digest=subject.digest,
            fixed_work_object={
                "subject_digest": subject.digest,
                "spec_documents": [document.name for document in subject.documents],
            },
            mentions=mentions,
            error_type=PlanningError,
            subject_name="Plan",
        )
        return PlanReviewResult(
            subject_digest=review.subject_digest,
            decision=review.decision,
            summary=review.summary,
            required_changes=review.required_changes,
            audit_artifact=review.audit_artifact,
        )

    def review_milestones(
        self,
        request: MilestoneReviewRequest,
    ) -> MilestoneReviewResult:
        """Review one exact complete Milestone View with the same generic Reviewer."""
        serialized = json.dumps(
            [
                {
                    "key": milestone.key,
                    "objective": milestone.objective,
                    "state": milestone.state.value,
                    "stages": [
                        {"key": stage.key, "objective": stage.objective}
                        for stage in milestone.stages
                    ],
                }
                for milestone in request.milestones
            ],
            ensure_ascii=True,
            separators=(",", ":"),
        )

        def mentions(workspace: Path) -> tuple[tuple[str, Path], ...]:
            subject = workspace / "inputs" / "milestones.json"
            subject.parent.mkdir(parents=True, exist_ok=True)
            subject.write_text(serialized, encoding="utf-8")
            return (("milestone-view", subject),)

        review = self._review_exact_subject(
            triage_id=request.triage_id,
            role=PromptRole.MILESTONE_HARD_GATE,
            subject_digest=request.subject_digest,
            fixed_work_object={
                "subject_digest": request.subject_digest,
                "plan_commit_sha": request.plan_commit_sha,
            },
            mentions=mentions,
            error_type=DeliveryError,
            subject_name="Milestone View",
        )
        return MilestoneReviewResult(
            subject_digest=review.subject_digest,
            decision=review.decision,
            summary=review.summary,
            required_changes=review.required_changes,
            audit_artifact=review.audit_artifact,
        )

    def _review_exact_subject(
        self,
        *,
        triage_id: str,
        role: PromptRole,
        subject_digest: str,
        fixed_work_object: dict[str, object],
        mentions: Callable[[Path], tuple[tuple[str, Path], ...]],
        error_type: type[ValueError],
        subject_name: str,
    ) -> _HardGateReview:
        card = self.collaboration.catalog.get(
            self.collaboration.catalog.plan_reviewer_id
        )
        if card.role is not AgentRole.REVIEWER:
            raise RuntimeError("Configured Plan Hard Gate Agent is not a Reviewer")
        workspace = self.collaboration.workspaces.create(card)
        invocation = self.collaboration.workspaces.create_invocation(workspace)
        try:
            self.collaboration.transport.run(
                CodexTurnRequest(
                    thread_id=None,
                    workspace=workspace.path,
                    developer_instructions=self.collaboration.prompts.role_instructions(
                        role,
                        profile_instructions=card.profile_instructions,
                    ),
                    message="\n\n".join(
                        (
                            self.collaboration.prompts.render_invocation(
                                InvocationContract(
                                    role=role,
                                    operation=role.value,
                                    project_root=(
                                        self.collaboration.workspaces.project_path
                                    ),
                                    observation_skill=(
                                        self.collaboration.observation_skill
                                    ),
                                    triage_id=triage_id,
                                    fixed_work_object=fixed_work_object,
                                    workspace={
                                        "write_scope": "fresh_reviewer_workspace",
                                        "project_and_runtime": "read_only",
                                    },
                                    output_contract={
                                        "result_path": str(invocation.result_path),
                                        "manifest_schema": (
                                            _HardGateManifest.model_json_schema()
                                        ),
                                        "subject_contract": {
                                            "subject_digest": subject_digest,
                                            "pass_required_changes": [],
                                            "revise_required_changes": (
                                                "one_or_more_concrete_strings"
                                            ),
                                        },
                                        "artifact_contract": {
                                            "path": "documents/review.md",
                                            "media_type": "text/markdown",
                                        },
                                        "final_response": {
                                            "format": "json",
                                            "required_fields": ["summary"],
                                        },
                                    },
                                )
                            ),
                            self.collaboration.prompts.task_instructions(role),
                        )
                    ),
                    mentions=mentions(workspace.path),
                    output_schema=_SUMMARY_OUTPUT_SCHEMA,
                )
            )
            manifest = _HardGateManifest.model_validate(
                self.collaboration.workspaces.read_result_json(invocation)
            )
            summary = " ".join(manifest.summary.split())
            required_changes = tuple(
                normalized
                for change in manifest.required_changes
                if (normalized := " ".join(change.split()))
            )
            if manifest.subject_digest != subject_digest:
                raise AgentCollaborationError(
                    f"Reviewer result does not identify the supplied {subject_name}"
                )
            if not summary:
                raise AgentCollaborationError("Reviewer result summary is empty")
            if manifest.decision == "pass" and required_changes:
                raise AgentCollaborationError(
                    "Reviewer pass result must not contain required changes"
                )
            if manifest.decision == "revise" and not required_changes:
                raise AgentCollaborationError(
                    "Reviewer revise result must contain required changes"
                )
            artifact = manifest.artifacts[0]
            audit = self.collaboration.workspaces.output_artifact(
                workspace,
                artifact.path,
                expected_name="review.md",
            )
        except (AgentCollaborationError, ValidationError) as error:
            raise error_type(f"{subject_name} Hard Gate failed: {error}") from error
        return _HardGateReview(
            subject_digest=manifest.subject_digest,
            decision=manifest.decision,
            summary=summary[:2_000],
            required_changes=required_changes,
            audit_artifact=audit,
        )
