"""Private Stage execution port and Codex adapter."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agentplanex.agent_contracts import InvocationContract, PromptRole
from agentplanex.domains import Milestone, Stage, StageRun
from agentplanex.infrastructure.codex import CodexTurnRequest, CodexTurnTransport
from agentplanex.services.agent_contracts import (
    AgentPromptCatalog,
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


class _StageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    summary: str = Field(min_length=1)


@dataclass(frozen=True, slots=True)
class CodexStageExecutor:
    """Run each Stage in a fresh Codex thread inside its Candidate worktree."""

    project_path: Path
    transport: CodexTurnTransport
    observation_skill: Path
    prompts: AgentPromptCatalog

    def execute(self, request: StageExecutionRequest) -> None:
        relative_document = self._relative_document(request)
        turn = self.transport.run(
            CodexTurnRequest(
                thread_id=None,
                workspace=request.worktree,
                developer_instructions=self.prompts.role_instructions(
                    PromptRole.STAGE_EXECUTOR
                ),
                message=self._prompt(request, relative_document),
                mentions=(),
                output_schema=_STAGE_OUTPUT_SCHEMA,
            )
        )
        try:
            response = _StageResponse.model_validate_json(turn.final_response)
        except ValidationError as error:
            raise StageExecutorError(
                "Stage Executor final response does not contain a valid summary"
            ) from error
        summary = " ".join(response.summary.split())
        if not summary:
            raise StageExecutorError("Stage Executor returned an empty summary")

    @staticmethod
    def _relative_document(request: StageExecutionRequest) -> Path:
        try:
            return request.delivery_document.resolve().relative_to(
                request.worktree.resolve()
            )
        except ValueError as error:
            raise StageExecutorError(
                "Stage delivery document is outside the delivery worktree"
            ) from error

    def _prompt(
        self,
        request: StageExecutionRequest,
        relative_document: Path,
    ) -> str:
        contract = {
            "stage_run_id": request.stage_run.stage_run_id,
            "run_id": request.stage_run.run_id,
            "snapshot_id": request.stage_run.snapshot_id,
            "milestone": {
                "key": request.milestone.key,
                "objective": request.milestone.objective,
            },
            "stage": {
                "key": request.stage.key,
                "objective": request.stage.objective,
            },
            "input_commit_sha": request.stage_run.input_commit_sha,
            "delivery_document": relative_document.as_posix(),
        }
        return "\n\n".join(
            (
                json.dumps(contract, ensure_ascii=True, indent=2),
                self.prompts.render_invocation(
                    InvocationContract(
                        role=PromptRole.STAGE_EXECUTOR,
                        operation="execute_stage",
                        project_root=self.project_path,
                        observation_skill=self.observation_skill,
                        triage_id=request.stage_run.triage_id,
                        fixed_work_object={
                            "stage_run_id": request.stage_run.stage_run_id,
                            "snapshot_id": request.stage_run.snapshot_id,
                            "input_commit_sha": request.stage_run.input_commit_sha,
                        },
                        workspace={
                            "write_scope": str(request.worktree.resolve()),
                            "git_commits_and_refs": "forbidden",
                            "runtime_sqlite": "forbidden",
                        },
                        output_contract={
                            "implementation": "uncommitted_project_changes",
                            "delivery_document": relative_document.as_posix(),
                            "final_response": {
                                "format": "json",
                                "required_fields": ["summary"],
                            },
                        },
                    )
                ),
                self.prompts.task_instructions(PromptRole.STAGE_EXECUTOR),
            )
        )
