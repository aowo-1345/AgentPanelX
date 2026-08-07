"""Real Codex-backed Plan Hard Gate using a configured generic Reviewer."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agentplanex.domains import AgentCollaborationError, AgentRole, ArtifactDescriptor
from agentplanex.infrastructure.codex import CodexTurnRequest
from agentplanex.services.agent_collaboration import AgentCollaborationService
from agentplanex.services.agent_contracts import render_invocation_envelope
from agentplanex.services.delivery import (
    DeliveryError,
    MilestoneReviewRequest,
    MilestoneReviewResult,
)
from agentplanex.services.planning import (
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
        review = self._review_exact_subject(
            triage_id=request.triage_id,
            role="plan_hard_gate",
            subject_digest=request.subject_digest,
            fixed_work_object={
                "subject_digest": request.subject_digest,
                "spec_documents": [str(path) for path in request.spec_documents],
            },
            prompt=lambda result_path: self._prompt(request, result_path),
            mentions=lambda _workspace: tuple(
                (f"plan-{index + 1}-{document.name}", document)
                for index, document in enumerate(request.spec_documents)
            ),
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
            role="milestone_hard_gate",
            subject_digest=request.subject_digest,
            fixed_work_object={
                "subject_digest": request.subject_digest,
                "plan_commit_sha": request.plan_commit_sha,
            },
            prompt=lambda result_path: self._milestone_prompt(request, result_path),
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
        role: str,
        subject_digest: str,
        fixed_work_object: dict[str, object],
        prompt: Callable[[Path], str],
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
        gate_name = "Plan" if role == "plan_hard_gate" else "Milestone"
        try:
            self.collaboration.transport.run(
                CodexTurnRequest(
                    thread_id=None,
                    workspace=workspace.path,
                    developer_instructions=(
                        "You are an AgentPlaneX Hard Gate Reviewer. Evaluate only the "
                        "fixed subject supplied by Runtime, cite evidence, and return "
                        "the required gate Contract. You must not make the Owner's "
                        "decision, implement changes, follow a newer current pointer, "
                        "or modify project source, Git refs, or Runtime data.\n\n"
                        f"Configured profile instructions:\n{card.developer_instructions}\n\n"
                        f"This invocation is a protected {gate_name} Hard Gate. Do not "
                        "treat the decision as optional advice."
                    ),
                    message="\n\n".join(
                        (
                            render_invocation_envelope(
                                role=role,
                                operation=role,
                                project_root=self.collaboration.workspaces.project_path,
                                observation_skill=self.collaboration.observation_skill,
                                triage_id=triage_id,
                                fixed_work_object=fixed_work_object,
                                workspace="Fresh isolated Reviewer workspace only.",
                                output_contract=(
                                    "pass|revise decision, required changes, exact "
                                    "review.md, and a short JSON summary."
                                ),
                            ),
                            prompt(invocation.result_path),
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

    @staticmethod
    def _prompt(request: PlanReviewRequest, result_path: Path) -> str:
        return "\n\n".join(
            (
                "Review the three attached Plan specification documents as one exact "
                "subject. Determine whether the protected Plan approval action may proceed.",
                f"The Runtime-computed subject digest is: {request.subject_digest}",
                "Write a complete Markdown review to documents/review.md in your current "
                "Reviewer workspace. Include concrete evidence and required changes.",
                "Before the final response, write a UTF-8 JSON object to exactly "
                f"{result_path}. It must contain only version=1, subject_digest, "
                "decision=pass|revise, summary, required_changes, and one artifacts entry "
                'with path="documents/review.md" and media_type="text/markdown".',
                "Echo the supplied digest exactly. A pass requires an empty "
                "required_changes array; revise requires at least one concrete change. "
                "Return only a short JSON summary in the final response and do not copy "
                "the review document into it.",
            )
        )

    @staticmethod
    def _milestone_prompt(
        request: MilestoneReviewRequest,
        result_path: Path,
    ) -> str:
        return "\n\n".join(
            (
                "Review the attached complete Milestone View as one exact subject. "
                "Determine whether the protected Milestone publication action may proceed.",
                f"The Runtime-computed subject digest is: {request.subject_digest}",
                "Write a complete Markdown review to documents/review.md in your current "
                "Reviewer workspace. Include concrete evidence and required changes.",
                "Before the final response, write a UTF-8 JSON object to exactly "
                f"{result_path}. It must contain only version=1, subject_digest, "
                "decision=pass|revise, summary, required_changes, and one artifacts entry "
                'with path="documents/review.md" and media_type="text/markdown".',
                "Echo the supplied digest exactly. A pass requires an empty "
                "required_changes array; revise requires at least one concrete change. "
                "Return only a short JSON summary in the final response and do not copy "
                "the review document into it.",
            )
        )
