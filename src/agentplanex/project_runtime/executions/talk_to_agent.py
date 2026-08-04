"""Project Runtime execution for synchronous Planner/Reviewer collaboration."""

from uuid import uuid4

from agentplanex.domains import (
    AgentCollaborationError,
    AgentInteractionKind,
    ArtifactRef,
    ExecutionEvent,
    ExecutionEventType,
    ProjectRuntimeContext,
    TalkToAgentRequest,
    ToolArguments,
    ToolExecutionResult,
)
from agentplanex.project_owner_agent.tools import (
    TALK_TO_AGENT_TOOL,
    ToolDefinition,
    create_talk_to_agent_tool,
)
from agentplanex.project_runtime.executions.base import (
    ProjectExecution,
    project_execution,
)


@project_execution(TALK_TO_AGENT_TOOL)
class TalkToAgentExecution(ProjectExecution):
    """Validate one Tool Action and synchronously invoke its configured Agent."""

    def tool_definition(self) -> ToolDefinition:
        return create_talk_to_agent_tool(
            self.dependencies.collaboration.catalog.card_description()
        )

    def execute(
        self,
        context: ProjectRuntimeContext,
        arguments: ToolArguments,
    ) -> ToolExecutionResult:
        try:
            request = self._request(arguments)
            self.dependencies.collaboration.catalog.get(request.agent_id)
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
                    "resumed": request.conversation_id is not None,
                    "input_artifact_count": len(request.artifacts),
                },
            )
        )
        try:
            result = self.dependencies.collaboration.talk(request)
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
                    "resumed": request.conversation_id is not None,
                    "input_artifact_count": len(request.artifacts),
                    "output_artifacts": [
                        {
                            "uri": artifact.uri,
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
                "conversation_id": result.conversation_id,
                "summary": result.summary,
                "artifacts": [
                    {
                        "uri": artifact.uri,
                        "media_type": artifact.media_type,
                        "size": artifact.size,
                        "sha256": artifact.sha256,
                    }
                    for artifact in result.artifacts
                ],
            }
        )

    def _publish_failure(
        self,
        context: ProjectRuntimeContext,
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
                    "resumed": request.conversation_id is not None,
                    "failure_type": type(error).__name__,
                },
            )
        )

    @staticmethod
    def _request(arguments: ToolArguments) -> TalkToAgentRequest:
        allowed = {"agent_id", "kind", "message", "conversation_id", "artifacts"}
        unknown = set(arguments) - allowed
        if unknown:
            raise AgentCollaborationError(
                "talk_to_agent received unsupported arguments: "
                + ", ".join(sorted(unknown))
            )
        agent_id = arguments.get("agent_id")
        kind = arguments.get("kind")
        message = arguments.get("message")
        conversation_id = arguments.get("conversation_id")
        artifacts = arguments.get("artifacts")
        if not isinstance(agent_id, str) or not agent_id.strip():
            raise AgentCollaborationError("talk_to_agent requires a non-empty agent_id")
        if not isinstance(kind, str):
            raise AgentCollaborationError("talk_to_agent requires kind=message or task")
        try:
            interaction = AgentInteractionKind(kind)
        except ValueError as error:
            raise AgentCollaborationError(
                "talk_to_agent kind must be message or task"
            ) from error
        if not isinstance(message, str) or not message.strip():
            raise AgentCollaborationError("talk_to_agent message must not be empty")
        if conversation_id is not None and not isinstance(conversation_id, str):
            raise AgentCollaborationError("conversation_id must be a string when provided")
        if not isinstance(artifacts, list):
            raise AgentCollaborationError("talk_to_agent artifacts must be an array")
        refs: list[ArtifactRef] = []
        for item in artifacts:
            if not isinstance(item, dict) or set(item) != {"uri"}:
                raise AgentCollaborationError(
                    "each talk_to_agent artifact must contain only a uri"
                )
            uri = item.get("uri")
            if not isinstance(uri, str) or not uri.strip():
                raise AgentCollaborationError("artifact uri must not be empty")
            refs.append(ArtifactRef(uri=uri))
        return TalkToAgentRequest(
            agent_id=agent_id,
            kind=interaction,
            message=message,
            conversation_id=conversation_id,
            artifacts=tuple(refs),
        )
