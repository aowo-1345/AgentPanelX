"""Plan business adapter for the shared External Agent Runtime."""

import base64
from dataclasses import dataclass
from typing import cast

from agentplanex.services._hard_gate import (
    GateResource,
    HardGateOutput,
    HardGatePayload,
)
from agentplanex.services.external_agent_runtime import (
    ExternalAgentRequest,
    ExternalAgentRuntime,
    ManagedAgentScope,
)
from agentplanex.services.planning.contracts import (
    PlanningError,
    PlanReviewRequest,
    PlanReviewResult,
)


@dataclass(frozen=True, slots=True)
class PlanHardGate:
    runtime: ExternalAgentRuntime

    def review(self, request: PlanReviewRequest) -> PlanReviewResult:
        subject = request.subject
        try:
            result = self.runtime.invoke(
                ExternalAgentRequest(
                    agent_key="plan_hard_gate",
                    operation_key="plan_hard_gate_v1",
                    request_key=f"plan:{subject.digest}",
                    scope=ManagedAgentScope(triage_id=request.triage_id),
                    payload=HardGatePayload(
                        triage_id=request.triage_id,
                        subject_name="Plan",
                        subject_digest=subject.digest,
                        task="Review this exact Plan for approval readiness.",
                        runtime_context={},
                        resources=tuple(
                            GateResource(
                                name=document.name,
                                content_base64=base64.b64encode(document.content).decode("ascii"),
                            )
                            for document in subject.documents
                        ),
                    ),
                ),
            ).output
            result = cast(HardGateOutput, result)
        except Exception as error:
            raise PlanningError(f"Plan Hard Gate failed closed: {error}") from error
        return PlanReviewResult(
            subject_digest=result.subject_digest,
            decision=result.decision,
            summary=result.summary,
            required_changes=result.required_changes,
            audit_artifact=result.audit_artifact,
        )
