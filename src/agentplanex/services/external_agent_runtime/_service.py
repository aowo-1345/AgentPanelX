"""Single invocation boundary for all Owner-external Agents."""

from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar, cast

from pydantic import BaseModel

from agentplanex.domains.artifact import ArtifactDescriptor
from agentplanex.infrastructure.agent_workspace import (
    AgentInvocation,
    AgentWorkspace,
    AgentWorkspaceStore,
    ResolvedArtifact,
)
from agentplanex.infrastructure.codex import (
    CodexTransportUnsafeTimeout,
    CodexTurnRequest,
)
from agentplanex.services.external_agent_runtime.contracts import AgentOperation
from agentplanex.services.external_agent_runtime.models import (
    AgentDefinition,
    ExecutionPolicy,
    ExternalAgentRequest,
    ExternalAgentResult,
    PreparedAgentTurn,
    SessionPolicy,
)

InputT = TypeVar("InputT", bound=BaseModel)


class ExternalAgentRuntimeError(ValueError):
    """The shared invocation or its static Contract is invalid."""


@dataclass(frozen=True, slots=True)
class AgentInvocationContext:
    """Bounded workspace capabilities exposed to a role Operation."""

    workspaces: AgentWorkspaceStore
    workspace: AgentWorkspace
    invocation: AgentInvocation
    activation_id: str

    @property
    def outbox_result_path(self) -> Path:
        return self.invocation.result_path

    def read_outbox_json(self) -> dict[str, Any]:
        return self.workspaces.read_result_json(self.invocation)

    def publish_artifact(
        self,
        relative_path: str,
        *,
        expected_name: str,
    ) -> ArtifactDescriptor:
        return self.workspaces.freeze_output_artifact(
            self.workspace,
            self.activation_id,
            relative_path,
            expected_name=expected_name,
        )

    def stage_input(
        self,
        name: str,
        content: bytes,
        *,
        media_type: str,
    ) -> ResolvedArtifact:
        return self.workspaces.stage_activation_input(
            self.workspace,
            self.activation_id,
            name,
            content,
            media_type=media_type,
        )


@dataclass(frozen=True, slots=True)
class ExternalAgentRuntime:
    """Select a Session, invoke Codex, validate, and publish one result."""

    workspaces: AgentWorkspaceStore
    transport: Any
    definitions: Mapping[str, AgentDefinition]
    operations: Mapping[tuple[str, str], object]

    def __post_init__(self) -> None:
        expected = {
            (definition.agent_key, operation_key)
            for definition in self.definitions.values()
            for operation_key in definition.allowed_operation_keys
        }
        if set(self.operations) != expected:
            raise ExternalAgentRuntimeError(
                "External Agent Definitions and registered Operations disagree"
            )

    def invoke(
        self,
        request: ExternalAgentRequest[InputT],
    ) -> ExternalAgentResult[Any]:
        try:
            definition = self.definitions[request.agent_key]
            registered = self.operations[(request.agent_key, request.operation_key)]
        except KeyError as error:
            raise ExternalAgentRuntimeError(
                "External Agent identity or Operation is not registered"
            ) from error
        operation = cast(AgentOperation[InputT, Any], registered)
        if operation.operation_key != request.operation_key:
            raise ExternalAgentRuntimeError("Registered Agent Operation is inconsistent")
        if operation.operation_key not in definition.allowed_operation_keys:
            raise ExternalAgentRuntimeError(
                f"Operation {operation.operation_key!r} is not allowed for {definition.agent_key!r}"
            )
        scope_key = self._scope_key(definition.session_policy, request)
        operation_digest = self._operation_digest(operation)
        protocol_digest = self._agent_protocol_digest(definition)
        while True:
            workspace = self.workspaces.get_or_create_managed(
                agent_id=definition.agent_key,
                profile_digest=protocol_digest,
                session_key=f"{definition.session_policy.value}:{scope_key}",
            )
            with self.workspaces.lock_session(workspace):
                if self.workspaces.is_quarantined(workspace):
                    continue
                return self._invoke_locked(
                    definition,
                    operation,
                    request,
                    workspace,
                    scope_key,
                    operation_digest,
                )

    def _invoke_locked(
        self,
        definition: AgentDefinition,
        operation: AgentOperation[InputT, Any],
        request: ExternalAgentRequest[InputT],
        workspace: AgentWorkspace,
        scope_key: str,
        operation_digest: str,
    ) -> ExternalAgentResult[Any]:
        payload_fingerprint = operation.request_fingerprint(request.payload)
        request_digest = self._digest(
            {
                "agent_key": definition.agent_key,
                "operation_key": operation.operation_key,
                "scope_key": scope_key,
                "operation_digest": operation_digest,
                "payload": payload_fingerprint,
            }
        )
        activation = self.workspaces.prepare_managed_invocation(
            workspace,
            request_key=request.request_key,
            request_digest=request_digest,
        )
        context = AgentInvocationContext(
            workspaces=self.workspaces,
            workspace=workspace,
            invocation=activation.invocation,
            activation_id=activation.activation_id,
        )
        if activation.result is not None:
            output = operation.load_result(activation.result, context)
            return ExternalAgentResult(
                request_key=request.request_key,
                output=output,
                replayed=True,
            )
        prepared = operation.prepare(request.payload, context)
        if operation.request_fingerprint(request.payload) != payload_fingerprint:
            raise ExternalAgentRuntimeError(
                "External Agent input changed while preparing the Activation"
            )
        self._require_execution_workspace(
            definition.execution_policy,
            prepared.execution_workspace,
        )
        thread_id = self.workspaces.load_managed_thread(workspace)
        try:
            turn = self.transport.run(
                CodexTurnRequest(
                    thread_id=thread_id,
                    workspace=(
                        prepared.execution_workspace or self.workspaces.execution_path(workspace)
                    ),
                    developer_instructions=definition.stable_instructions,
                    message=self._message(prepared),
                    mentions=tuple(
                        (
                            f"artifact-{index + 1}-{resource.path.name}",
                            resource.path,
                        )
                        for index, resource in enumerate(prepared.resources)
                    ),
                    skills=tuple((skill.name, skill.path) for skill in definition.bound_skills),
                    output_schema=operation.output_schema,
                ),
                on_thread_opened=lambda opened: self.workspaces.save_managed_thread(
                    workspace,
                    opened,
                ),
            )
        except CodexTransportUnsafeTimeout as error:
            self.workspaces.quarantine_session(workspace, str(error))
            raise
        output = operation.validate(request.payload, context, turn)
        dumped = operation.dump_result(output)
        self.workspaces.publish_managed_result(
            workspace,
            activation.activation_id,
            request_digest=request_digest,
            result=dumped,
        )
        return ExternalAgentResult(
            request_key=request.request_key,
            output=output,
        )

    @staticmethod
    def _scope_key(
        policy: SessionPolicy,
        request: ExternalAgentRequest[Any],
    ) -> str:
        if policy is SessionPolicy.FEATURE:
            return request.scope.triage_id
        if policy is SessionPolicy.ACTIVATION:
            return request.request_key
        if policy is SessionPolicy.STAGE_RUN:
            if request.scope.stage_run_id is None:
                raise ExternalAgentRuntimeError("A StageRun Session requires scope.stage_run_id")
            return request.scope.stage_run_id
        raise ExternalAgentRuntimeError(f"Unsupported Session Policy: {policy}")

    def _message(self, prepared: PreparedAgentTurn) -> str:
        return "\n\n".join(
            part.strip()
            for part in (
                prepared.task_text,
                (f"Runtime-managed Feature worktree: {self.workspaces.project_path.resolve()}"),
                prepared.runtime_context_text,
                prepared.control_text,
            )
            if part.strip()
        )

    @classmethod
    def _operation_digest(cls, operation: AgentOperation[Any, Any]) -> str:
        try:
            implementation = inspect.getsource(type(operation))
        except (OSError, TypeError) as error:
            raise ExternalAgentRuntimeError(
                "Registered Agent Operation source is unavailable"
            ) from error
        return cls._digest(
            {
                "implementation": implementation,
                "operation_key": operation.operation_key,
                "output_schema": operation.output_schema,
                "contract": operation.contract_fingerprint(),
            }
        )

    def _agent_protocol_digest(self, definition: AgentDefinition) -> str:
        return self._digest(
            {
                "definition": definition.protocol_digest,
                "operations": [
                    self._operation_digest(
                        cast(
                            AgentOperation[Any, Any],
                            self.operations[(definition.agent_key, operation_key)],
                        )
                    )
                    for operation_key in sorted(definition.allowed_operation_keys)
                ],
            }
        )

    def _require_execution_workspace(
        self,
        policy: ExecutionPolicy,
        requested: Path | None,
    ) -> None:
        if policy is ExecutionPolicy.AGENT_WORKSPACE and requested is not None:
            raise ExternalAgentRuntimeError(
                "Agent workspace policy forbids an external execution workspace"
            )
        if policy is ExecutionPolicy.CANDIDATE_WORKTREE and requested is None:
            raise ExternalAgentRuntimeError(
                "Candidate worktree policy requires an execution workspace"
            )
        if policy is ExecutionPolicy.TRUSTED_FEATURE_USER_PROXY and (
            requested is None or requested.resolve() != self.workspaces.project_path.resolve()
        ):
            raise ExternalAgentRuntimeError(
                "Trusted Feature user proxy must execute in its exact Feature worktree"
            )

    @staticmethod
    def _digest(payload: object) -> str:
        encoded = json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()
