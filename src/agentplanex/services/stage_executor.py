"""Fresh Codex execution for one fixed delivery Stage."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agentplanex.domains import Milestone, Stage, StageRun
from agentplanex.infrastructure.codex import CodexTurnRequest, CodexTurnTransport

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

    transport: CodexTurnTransport

    def execute(self, request: StageExecutionRequest) -> None:
        relative_document = self._relative_document(request)
        turn = self.transport.run(
            CodexTurnRequest(
                thread_id=None,
                workspace=request.worktree,
                developer_instructions=(
                    "You are the AgentPlaneX Stage Executor. Work only inside the "
                    "provided detached delivery worktree. Do not commit, merge, change "
                    "Git refs, or modify Runtime SQLite data."
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

    @staticmethod
    def _prompt(request: StageExecutionRequest, relative_document: Path) -> str:
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
                "This is a fixed AgentPlaneX Stage Contract.",
                json.dumps(contract, ensure_ascii=True, indent=2),
                "Implement only this Stage. Modify at least one project file in addition "
                "to the required delivery document. Do not edit the Milestone plan.",
                "Write a non-empty UTF-8 Markdown delivery document at the exact path "
                f"{relative_document.as_posix()}. Record the implementation and "
                "validation performed.",
                "Leave all changes uncommitted. Return only a JSON object containing one "
                "short summary field.",
            )
        )
