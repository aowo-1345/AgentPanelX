"""Synchronous Owner-to-external-Agent collaboration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from agentplanex.domains.project_runtime_state import ProjectRuntimeState
from agentplanex.infrastructure.agent_workspace import (
    AgentWorkspaceError,
)
from agentplanex.infrastructure.codex import CodexTransportError
from agentplanex.services.agent_collaboration._catalog import (
    AgentCatalog,
    DelegatedAgentRole,
)
from agentplanex.services.agent_collaboration._operations import A2AOperationOutput, A2APayload
from agentplanex.services.agent_collaboration.models import (
    AgentInteractionKind,
    TalkToAgentRequest,
    TalkToAgentResult,
)
from agentplanex.services.agent_invocation import AgentInvocationError
from agentplanex.services.external_agent_runtime import (
    ExternalAgentRequest,
    ExternalAgentRuntime,
    ExternalAgentRuntimeError,
    ManagedAgentScope,
)


class AgentCollaborationError(ValueError):
    """A delegated Agent request or result failed its business contract."""


@dataclass(frozen=True, slots=True)
class AgentCollaborationService:
    """Own A2A semantics while the shared Runtime owns invocation mechanics."""

    catalog: AgentCatalog
    runtime: ExternalAgentRuntime

    def describe_agents(self) -> str:
        return self.catalog.describe()

    def require_agent(self, agent_id: str) -> None:
        try:
            self.catalog.get(agent_id)
        except AgentInvocationError as error:
            raise AgentCollaborationError(str(error)) from error

    def talk(
        self,
        request: TalkToAgentRequest,
        context: ProjectRuntimeState,
    ) -> TalkToAgentResult:
        try:
            card = self.catalog.get(request.agent_id)
            message = request.message.strip()
            if not message:
                raise AgentCollaborationError("Agent message must not be empty")
            operation_key = self._operation_key(card.role, request.kind)
            result = self.runtime.invoke(
                ExternalAgentRequest(
                    agent_key=card.agent_id,
                    operation_key=operation_key,
                    request_key=request.request_key,
                    scope=ManagedAgentScope(triage_id=context.triage_id),
                    payload=A2APayload(
                        kind=request.kind,
                        message=message,
                        triage_id=context.triage_id,
                        status=context.status,
                        pending_action=context.pending_action,
                        plan_commit_sha=context.current_plan_commit_sha,
                        snapshot_id=context.current_snapshot_id,
                        run_id=context.current_run_id,
                        milestone_key=context.current_milestone_key,
                        stage_key=context.current_stage_key,
                        candidate_commit_sha=context.current_candidate_commit_sha,
                        artifact_uris=tuple(artifact.uri for artifact in request.artifacts),
                    ),
                ),
            )
            output = cast(A2AOperationOutput, result.output)
            return TalkToAgentResult(
                agent_id=card.agent_id,
                summary=output.summary,
                artifacts=output.artifacts,
            )
        except AgentCollaborationError:
            raise
        except (
            AgentInvocationError,
            AgentWorkspaceError,
            CodexTransportError,
            ExternalAgentRuntimeError,
            ValueError,
        ) as error:
            raise AgentCollaborationError(str(error)) from error

    @staticmethod
    def _operation_key(
        role: DelegatedAgentRole,
        kind: AgentInteractionKind,
    ) -> str:
        if role is DelegatedAgentRole.PLANNER:
            return f"planner_{kind.value}_v1"
        if role is DelegatedAgentRole.REVIEWER:
            return f"reviewer_{kind.value}_v1"
        return (
            "task_distribution_v1"
            if kind is AgentInteractionKind.TASK
            else "task_distributor_message_v1"
        )
