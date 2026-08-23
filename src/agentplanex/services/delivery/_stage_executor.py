"""Stage execution port and External Agent Runtime adapter."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agentplanex.infrastructure.codex import CodexTurnResult
from agentplanex.infrastructure.git_repository import GitRepository
from agentplanex.services.delivery.models import Milestone, Stage, StageRun
from agentplanex.services.external_agent_runtime import (
    AgentInvocationContext,
    ExternalAgentRequest,
    ExternalAgentRuntime,
    ManagedAgentScope,
    PreparedAgentTurn,
)

_STAGE_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
    "additionalProperties": False,
}


class StageExecutorError(RuntimeError):
    """A Stage executor returned an invalid result Contract."""


@dataclass(frozen=True, slots=True)
class StageExecutionRequest:
    """One immutable Stage Contract bound to its detached Git worktree."""

    stage_run: StageRun
    milestone: Milestone
    stage: Stage
    worktree: Path
    delivery_document: Path


class StageExecutor(Protocol):
    """Execute one fixed Stage without committing or changing Runtime state."""

    def execute(self, request: StageExecutionRequest) -> None: ...


class _StagePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    triage_id: str
    stage_run_id: str
    run_id: str
    snapshot_id: str
    milestone_key: str
    milestone_objective: str
    stage_key: str
    stage_objective: str
    input_commit_sha: str


class _StageOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    summary: str = Field(min_length=1)


@dataclass(frozen=True, slots=True)
class _StageOperation:
    operation_key: str = "stage_execution_v1"
    output_schema: ClassVar[dict[str, Any]] = _STAGE_OUTPUT_SCHEMA

    def contract_fingerprint(self) -> object:
        return {"operation_key": self.operation_key, "result": "summary"}

    @staticmethod
    def request_fingerprint(payload: _StagePayload) -> object:
        return payload.model_dump(mode="json")

    def prepare(
        self,
        payload: _StagePayload,
        context: AgentInvocationContext,
    ) -> PreparedAgentTurn:
        worktree = GitRepository(context.workspaces.project_path).delivery_worktree_path(
            payload.run_id
        )
        relative_document = (
            Path("docs") / "agentplanex" / "deliveries" / payload.run_id / f"{payload.stage_key}.md"
        )
        contract = {
            "stage_run_id": payload.stage_run_id,
            "run_id": payload.run_id,
            "snapshot_id": payload.snapshot_id,
            "milestone": {
                "key": payload.milestone_key,
                "objective": payload.milestone_objective,
            },
            "stage": {
                "key": payload.stage_key,
                "objective": payload.stage_objective,
            },
            "input_commit_sha": payload.input_commit_sha,
            "delivery_document": relative_document.as_posix(),
        }
        return PreparedAgentTurn(
            task_text=f"Execute Stage {payload.stage_key}: {payload.stage_objective}",
            runtime_context_text=(
                "Fixed StageRun contract:\n"
                + json.dumps(contract, ensure_ascii=False, sort_keys=True)
            ),
            control_text=(
                "Leave all Candidate changes uncommitted, write the declared delivery "
                "document, and return only a JSON object with one short summary."
            ),
            execution_workspace=worktree,
        )

    def validate(
        self,
        _payload: _StagePayload,
        _context: AgentInvocationContext,
        turn: CodexTurnResult,
    ) -> _StageOutput:
        try:
            output = _StageOutput.model_validate_json(turn.final_response)
        except ValidationError as error:
            raise StageExecutorError(
                "Stage Executor final response does not contain a valid summary"
            ) from error
        normalized = " ".join(output.summary.split())
        if not normalized:
            raise StageExecutorError("Stage Executor returned an empty summary")
        return _StageOutput(summary=normalized)

    @staticmethod
    def dump_result(output: _StageOutput) -> dict[str, Any]:
        return output.model_dump(mode="json")

    @staticmethod
    def load_result(
        payload: dict[str, Any],
        _context: AgentInvocationContext,
    ) -> _StageOutput:
        return _StageOutput.model_validate(payload)


@dataclass(frozen=True, slots=True)
class CodexStageExecutor:
    """Run a Stage through the shared Runtime in its StageRun Session."""

    runtime: ExternalAgentRuntime

    def execute(self, request: StageExecutionRequest) -> None:
        stage_run = request.stage_run
        expected_worktree = GitRepository(
            self.runtime.workspaces.project_path
        ).delivery_worktree_path(stage_run.run_id)
        expected_document = (
            expected_worktree
            / "docs"
            / "agentplanex"
            / "deliveries"
            / stage_run.run_id
            / f"{request.stage.key}.md"
        )
        if request.worktree.resolve() != expected_worktree:
            raise StageExecutorError(
                "Stage worktree is not Runtime-managed: "
                f"expected {expected_worktree}, got {request.worktree.resolve()}"
            )
        if request.delivery_document.resolve() != expected_document:
            raise StageExecutorError("Stage delivery document is not Runtime-managed")
        self.runtime.invoke(
            ExternalAgentRequest(
                agent_key="stage_executor",
                operation_key="stage_execution_v1",
                request_key=stage_run.stage_run_id,
                scope=ManagedAgentScope(
                    triage_id=stage_run.triage_id,
                    stage_run_id=stage_run.stage_run_id,
                ),
                payload=_StagePayload(
                    triage_id=stage_run.triage_id,
                    stage_run_id=stage_run.stage_run_id,
                    run_id=stage_run.run_id,
                    snapshot_id=stage_run.snapshot_id,
                    milestone_key=request.milestone.key,
                    milestone_objective=request.milestone.objective,
                    stage_key=request.stage.key,
                    stage_objective=request.stage.objective,
                    input_commit_sha=stage_run.input_commit_sha,
                ),
            ),
        )
