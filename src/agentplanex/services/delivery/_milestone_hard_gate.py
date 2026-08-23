"""Milestone business adapter for the shared External Agent Runtime."""

import base64
import json
from dataclasses import dataclass
from typing import cast

from agentplanex.services._hard_gate import (
    GateResource,
    HardGateOutput,
    HardGatePayload,
)
from agentplanex.services.delivery.contracts import (
    DeliveryError,
    MilestoneReviewRequest,
    MilestoneReviewResult,
)
from agentplanex.services.external_agent_runtime import (
    ExternalAgentRequest,
    ExternalAgentRuntime,
    ManagedAgentScope,
)


@dataclass(frozen=True, slots=True)
class MilestoneHardGate:
    runtime: ExternalAgentRuntime

    def review(self, request: MilestoneReviewRequest) -> MilestoneReviewResult:
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
        ).encode("utf-8")
        try:
            result = self.runtime.invoke(
                ExternalAgentRequest(
                    agent_key="milestone_hard_gate",
                    operation_key="milestone_hard_gate_v1",
                    request_key=f"milestone:{request.subject_digest}",
                    scope=ManagedAgentScope(triage_id=request.triage_id),
                    payload=HardGatePayload(
                        triage_id=request.triage_id,
                        subject_name="Milestone View",
                        subject_digest=request.subject_digest,
                        task="Review this exact Milestone View against its approved Plan.",
                        runtime_context={
                            "plan_commit_sha": request.plan_commit_sha,
                        },
                        resources=(
                            GateResource(
                                name="milestones.json",
                                content_base64=base64.b64encode(serialized).decode("ascii"),
                            ),
                        ),
                    ),
                ),
            ).output
            result = cast(HardGateOutput, result)
        except Exception as error:
            raise DeliveryError(f"Milestone Hard Gate failed closed: {error}") from error
        return MilestoneReviewResult(
            subject_digest=result.subject_digest,
            decision=result.decision,
            summary=result.summary,
            required_changes=result.required_changes,
            audit_artifact=result.audit_artifact,
        )
