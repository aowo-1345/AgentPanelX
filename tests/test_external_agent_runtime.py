"""Behavior tests for the shared External Agent invocation boundary."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, ClassVar

import pytest
from pydantic import BaseModel, ConfigDict

from agentplanex.domains.artifact import ArtifactDescriptor
from agentplanex.infrastructure.agent_workspace import (
    AgentWorkspaceError,
    AgentWorkspaceStore,
)
from agentplanex.infrastructure.codex import (
    CodexTransportUnsafeTimeout,
    CodexTurnRequest,
    CodexTurnResult,
)
from agentplanex.services.agent_collaboration._operations import (
    A2AOperation,
    A2APayload,
)
from agentplanex.services.agent_collaboration.models import AgentInteractionKind
from agentplanex.services.external_agent_runtime import (
    AgentDefinition,
    AgentInvocationContext,
    AgentSkill,
    ExecutionPolicy,
    ExternalAgentRequest,
    ExternalAgentRuntime,
    ManagedAgentScope,
    PreparedAgentTurn,
    SessionPolicy,
)


class _Payload(BaseModel):
    model_config = ConfigDict(frozen=True)

    task: str


class _Output(BaseModel):
    model_config = ConfigDict(frozen=True)

    summary: str
    artifacts: tuple[ArtifactDescriptor, ...]


class _Operation:
    operation_key = "record_note_v1"
    output_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"summary": {"type": "string"}},
        "required": ["summary"],
        "additionalProperties": False,
    }

    def contract_fingerprint(self) -> object:
        return {"operation_key": self.operation_key, "document_name": "note.md"}

    @staticmethod
    def request_fingerprint(payload: _Payload) -> object:
        return payload.model_dump(mode="json")

    def prepare(
        self,
        payload: _Payload,
        context: AgentInvocationContext,
    ) -> PreparedAgentTurn:
        return PreparedAgentTurn(
            task_text=payload.task,
            runtime_context_text="Runtime status: IN_PROGRESS",
            resources=(),
            control_text=(f"Write the result manifest to {context.outbox_result_path}."),
        )

    def validate(
        self,
        _payload: _Payload,
        context: AgentInvocationContext,
        turn: CodexTurnResult,
    ) -> _Output:
        manifest = context.read_outbox_json()
        assert manifest == {"summary": "completed"}
        response = json.loads(turn.final_response)
        artifact = context.publish_artifact(
            "documents/note.md",
            expected_name="note.md",
        )
        return _Output(summary=response["summary"], artifacts=(artifact,))

    def dump_result(self, output: _Output) -> dict[str, Any]:
        return output.model_dump(mode="json")

    def load_result(
        self,
        payload: dict[str, Any],
        _context: AgentInvocationContext,
    ) -> _Output:
        return _Output.model_validate(payload)


@dataclass(slots=True)
class _RecordingTransport:
    requests: list[CodexTurnRequest] = field(default_factory=list)

    def run(
        self,
        request: CodexTurnRequest,
        *,
        on_thread_opened: Any,
    ) -> CodexTurnResult:
        self.requests.append(request)
        thread_id = request.thread_id or "feature-thread"
        on_thread_opened(thread_id)
        outbox = max(
            (request.workspace / "outbox").iterdir(),
            key=lambda path: path.stat().st_mtime_ns,
        )
        (outbox / "result.json").write_text(
            json.dumps({"summary": "completed"}),
            encoding="utf-8",
        )
        (request.workspace / "documents" / "note.md").write_text(
            f"# {len(self.requests)}\n",
            encoding="utf-8",
        )
        return CodexTurnResult(
            thread_id=thread_id,
            turn_id=f"turn-{len(self.requests)}",
            status="completed",
            final_response='{"summary":"completed"}',
        )


def test_feature_session_uses_native_skills_resumes_and_replays_valid_result(
    initialize_git_project: Any,
) -> None:
    project_path = initialize_git_project()
    skill = project_path / "observe" / "SKILL.md"
    skill.parent.mkdir()
    skill.write_text("# Observe\n", encoding="utf-8")
    definition = AgentDefinition(
        agent_key="planner",
        stable_instructions="You are the stable Planner.",
        session_policy=SessionPolicy.FEATURE,
        bound_skills=(AgentSkill(name="observe", path=skill),),
        execution_policy=ExecutionPolicy.AGENT_WORKSPACE,
        allowed_operation_keys=("record_note_v1",),
        protocol_digest="a" * 64,
    )
    transport = _RecordingTransport()
    runtime = ExternalAgentRuntime(
        workspaces=AgentWorkspaceStore(project_path, 65_536, 262_144),
        transport=transport,
        definitions={"planner": definition},
        operations={("planner", "record_note_v1"): _Operation()},
    )
    scope = ManagedAgentScope(triage_id="feature-1")

    first = runtime.invoke(
        ExternalAgentRequest(
            agent_key="planner",
            operation_key="record_note_v1",
            request_key="owner-activation-1:call-1",
            scope=scope,
            payload=_Payload(task="Create the first note."),
        ),
    )
    second_request = ExternalAgentRequest(
        agent_key="planner",
        operation_key="record_note_v1",
        request_key="owner-activation-2:call-2",
        scope=scope,
        payload=_Payload(task="Create the second note."),
    )
    second = runtime.invoke(second_request)
    replayed = runtime.invoke(second_request)

    assert first.output.summary == "completed"
    assert second == replayed
    assert len(transport.requests) == 2
    assert transport.requests[0].thread_id is None
    assert transport.requests[1].thread_id == "feature-thread"
    assert all(
        request.developer_instructions == "You are the stable Planner."
        for request in transport.requests
    )
    assert all(request.skills == (("observe", skill),) for request in transport.requests)
    assert "Observe" not in transport.requests[0].message
    assert "Create the first note." in transport.requests[0].message
    artifact = second.output.artifacts[0]
    assert "/artifacts/" in artifact.uri
    assert (project_path / artifact.project_relative_path).read_text(encoding="utf-8") == "# 2\n"
    first_artifact = first.output.artifacts[0]
    assert (project_path / first_artifact.project_relative_path).read_text(
        encoding="utf-8"
    ) == "# 1\n"

    with pytest.raises(AgentWorkspaceError, match="different input"):
        runtime.invoke(
            ExternalAgentRequest(
                agent_key="planner",
                operation_key="record_note_v1",
                request_key=second_request.request_key,
                scope=scope,
                payload=_Payload(task="Conflicting input."),
            ),
        )

    (project_path / artifact.project_relative_path).write_text(
        "# tampered\n",
        encoding="utf-8",
    )
    with pytest.raises(AgentWorkspaceError, match="integrity"):
        runtime.workspaces.resolve_artifact(artifact.uri)


@dataclass(slots=True)
class _UnsafeThenSuccessfulTransport:
    requests: list[CodexTurnRequest] = field(default_factory=list)

    def run(
        self,
        request: CodexTurnRequest,
        *,
        on_thread_opened: Any,
    ) -> CodexTurnResult:
        self.requests.append(request)
        on_thread_opened(f"thread-{len(self.requests)}")
        if len(self.requests) == 1:
            raise CodexTransportUnsafeTimeout("old process may still be alive")
        outbox = max(
            (request.workspace / "outbox").iterdir(),
            key=lambda path: path.stat().st_mtime_ns,
        )
        (outbox / "result.json").write_text(
            json.dumps({"summary": "completed"}),
            encoding="utf-8",
        )
        (request.workspace / "documents" / "note.md").write_text(
            "# recovered\n",
            encoding="utf-8",
        )
        return CodexTurnResult(
            thread_id="thread-2",
            turn_id="turn-2",
            status="completed",
            final_response='{"summary":"completed"}',
        )


def test_unsafe_timeout_quarantines_session_before_retry(
    initialize_git_project: Any,
) -> None:
    project_path = initialize_git_project()
    definition = AgentDefinition(
        agent_key="planner",
        stable_instructions="Stable.",
        session_policy=SessionPolicy.FEATURE,
        bound_skills=(),
        execution_policy=ExecutionPolicy.AGENT_WORKSPACE,
        allowed_operation_keys=("record_note_v1",),
        protocol_digest="b" * 64,
    )
    transport = _UnsafeThenSuccessfulTransport()
    runtime = ExternalAgentRuntime(
        workspaces=AgentWorkspaceStore(project_path, 65_536, 262_144),
        transport=transport,
        definitions={"planner": definition},
        operations={("planner", "record_note_v1"): _Operation()},
    )
    request = ExternalAgentRequest(
        agent_key="planner",
        operation_key="record_note_v1",
        request_key="activation:call",
        scope=ManagedAgentScope(triage_id="feature-1"),
        payload=_Payload(task="Recover safely."),
    )

    with pytest.raises(CodexTransportUnsafeTimeout):
        runtime.invoke(request)
    recovered = runtime.invoke(request)

    assert recovered.output.summary == "completed"
    assert len(transport.requests) == 2
    assert transport.requests[0].workspace != transport.requests[1].workspace
    assert transport.requests[1].thread_id is None


def test_replay_rejects_changed_mutable_attachment(
    initialize_git_project: Any,
) -> None:
    project_path = initialize_git_project()
    attachment = project_path / "requirements.md"
    attachment.write_text("# First\n", encoding="utf-8")
    definition = AgentDefinition(
        agent_key="planner",
        stable_instructions="Stable.",
        session_policy=SessionPolicy.FEATURE,
        bound_skills=(),
        execution_policy=ExecutionPolicy.AGENT_WORKSPACE,
        allowed_operation_keys=("planner_message_v1",),
        protocol_digest="c" * 64,
    )
    workspaces = AgentWorkspaceStore(project_path, 65_536, 262_144)
    transport = _RecordingTransport()
    runtime = ExternalAgentRuntime(
        workspaces=workspaces,
        transport=transport,
        definitions={"planner": definition},
        operations={
            ("planner", "planner_message_v1"): A2AOperation(
                "planner_message_v1",
                None,
                workspaces,
            )
        },
    )
    request = ExternalAgentRequest(
        agent_key="planner",
        operation_key="planner_message_v1",
        request_key="owner:call",
        scope=ManagedAgentScope(triage_id="feature-1"),
        payload=A2APayload(
            kind=AgentInteractionKind.MESSAGE,
            message="Inspect the requirements.",
            triage_id="feature-1",
            status="IN_PROGRESS",
            pending_action=None,
            plan_commit_sha=None,
            snapshot_id=None,
            run_id=None,
            milestone_key=None,
            stage_key=None,
            candidate_commit_sha=None,
            artifact_uris=("project:///requirements.md",),
        ),
    )

    runtime.invoke(request)
    attachment.write_text("# Changed\n", encoding="utf-8")

    with pytest.raises(AgentWorkspaceError, match="different input"):
        runtime.invoke(request)
