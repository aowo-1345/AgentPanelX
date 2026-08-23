"""Project Runtime execution for synchronous Planner/Reviewer collaboration."""

from typing import Annotated, Literal
from uuid import uuid4

from pydantic import Field, StringConstraints

from agentplanex.domains.execution_event import (
    ExecutionEvent,
    ExecutionEventType,
)
from agentplanex.domains.project_runtime_state import ProjectRuntimeState
from agentplanex.project_owner_agent.contracts import ToolExecutionResult
from agentplanex.project_owner_agent.tools import (
    NonBlankText,
    ToolArgumentsModel,
    ToolDefinition,
)
from agentplanex.project_runtime.executions.base import (
    ProjectExecution,
    project_execution,
)
from agentplanex.services.agent_collaboration import (
    AgentCollaborationError,
    AgentInteractionKind,
    ArtifactRef,
    TalkToAgentRequest,
)

TALK_TO_AGENT_TOOL_NAME = "talk_to_agent"
type AgentId = Annotated[
    str,
    StringConstraints(min_length=1, pattern=r"^[a-z][a-z0-9_-]{0,63}$"),
]
type ArtifactUri = Annotated[
    str,
    StringConstraints(
        min_length=1,
        pattern=r"^(project:///|artifact://local/).+",
    ),
]


class ArtifactInput(ToolArgumentsModel):
    uri: ArtifactUri = Field(
        description="Project or local artifact URI exposed by the Runtime."
    )


class TalkToAgentArguments(ToolArgumentsModel):
    agent_id: AgentId = Field(description="Configured Agent Card identifier.")
    kind: Literal["message", "task"] = Field(
        description="Use message for discussion or task for a role Contract document."
    )
    message: NonBlankText = Field(description="Instruction sent to the selected Agent.")
    artifacts: list[ArtifactInput] = Field(
        description="Read-only Runtime artifact inputs; use an empty array when absent."
    )

    def to_request(self, request_key: str) -> TalkToAgentRequest:
        return TalkToAgentRequest(
            request_key=request_key,
            agent_id=self.agent_id,
            kind=AgentInteractionKind(self.kind),
            message=self.message,
            artifacts=tuple(ArtifactRef(uri=artifact.uri) for artifact in self.artifacts),
        )


def create_talk_to_agent_tool(agent_cards: str) -> ToolDefinition[TalkToAgentArguments]:
    return ToolDefinition(
        name=TALK_TO_AGENT_TOOL_NAME,
        description=(
            "Synchronously send a Message or file-producing Task to a configured "
            "Planner, Reviewer, or Task Distributor. Message is a discussion turn with no "
            "document; Task publishes the role Contract document. Runtime automatically "
            "resumes role sessions according to policy. Pass returned artifact URIs as "
            "read-only inputs. Agent output is advisory until the Owner adopts it into "
            "canonical Specs; Reviewer output is evidence and never makes the Owner's "
            "decision. Available Agent Cards:\n"
            f"{agent_cards}"
        ),
        arguments_type=TalkToAgentArguments,
    )


TALK_TO_AGENT_TOOL = create_talk_to_agent_tool(
    "- planner (planner)\n- reviewer (reviewer)"
)


@project_execution(TALK_TO_AGENT_TOOL)
class TalkToAgentExecution(ProjectExecution[TalkToAgentArguments]):
    """Validate one Tool Action and synchronously invoke its configured Agent."""

    def tool_definition(self) -> ToolDefinition[TalkToAgentArguments]:
        return create_talk_to_agent_tool(
            self.dependencies.collaboration.describe_agents()
        )

    def execute(
        self,
        context: ProjectRuntimeState,
        arguments: TalkToAgentArguments,
    ) -> ToolExecutionResult:
        return self._execute(
            context,
            arguments,
            request_identity=uuid4().hex,
        )

    def execute_with_identity(
        self,
        context: ProjectRuntimeState,
        arguments: TalkToAgentArguments,
        *,
        request_identity: str | None,
    ) -> ToolExecutionResult:
        return self._execute(
            context,
            arguments,
            request_identity=request_identity or uuid4().hex,
        )

    def _execute(
        self,
        context: ProjectRuntimeState,
        arguments: TalkToAgentArguments,
        *,
        request_identity: str,
    ) -> ToolExecutionResult:
        request = arguments.to_request(request_identity)
        try:
            self.dependencies.collaboration.require_agent(request.agent_id)
        except AgentCollaborationError as error:
            return ToolExecutionResult(
                output={"ok": False, "error": str(error)},
            )

        invocation_id = uuid4().hex
        self.dependencies.event_bus.publish(
            ExecutionEvent(
                triage_id=context.triage_id,
                event_type=ExecutionEventType.AGENT_INVOCATION_STARTED,
                payload={
                    "invocation_id": invocation_id,
                    "operation": "talk_to_agent",
                    "agent_id": request.agent_id,
                    "kind": request.kind.value,
                    "input_artifact_count": len(request.artifacts),
                },
            )
        )
        try:
            result = self.dependencies.collaboration.talk(request, context)
        except AgentCollaborationError as error:
            self._publish_failure(context, request, invocation_id, error)
            return ToolExecutionResult(
                output={"ok": False, "error": str(error)},
            )
        except Exception as error:
            self._publish_failure(context, request, invocation_id, error)
            raise

        self.dependencies.event_bus.publish(
            ExecutionEvent(
                triage_id=context.triage_id,
                event_type=ExecutionEventType.AGENT_INVOCATION_COMPLETED,
                payload={
                    "invocation_id": invocation_id,
                    "operation": "talk_to_agent",
                    "agent_id": result.agent_id,
                    "kind": request.kind.value,
                    "input_artifact_count": len(request.artifacts),
                    "output_artifacts": [
                        {
                            "uri": artifact.uri,
                            "project_relative_path": artifact.project_relative_path,
                            "media_type": artifact.media_type,
                            "size": artifact.size,
                            "sha256": artifact.sha256,
                        }
                        for artifact in result.artifacts
                    ],
                },
            )
        )
        return ToolExecutionResult(
            output={
                "ok": True,
                "agent_id": result.agent_id,
                "summary": result.summary,
                "artifacts": [
                    {
                        "uri": artifact.uri,
                        "project_relative_path": artifact.project_relative_path,
                        "media_type": artifact.media_type,
                        "size": artifact.size,
                        "sha256": artifact.sha256,
                    }
                    for artifact in result.artifacts
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
        )

    def _publish_failure(
        self,
        context: ProjectRuntimeState,
        request: TalkToAgentRequest,
        invocation_id: str,
        error: Exception,
    ) -> None:
        self.dependencies.event_bus.publish(
            ExecutionEvent(
                triage_id=context.triage_id,
                event_type=ExecutionEventType.AGENT_INVOCATION_FAILED,
                payload={
                    "invocation_id": invocation_id,
                    "operation": "talk_to_agent",
                    "agent_id": request.agent_id,
                    "kind": request.kind.value,
                    "failure_type": type(error).__name__,
                },
            )
        )
