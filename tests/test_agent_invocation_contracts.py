"""Recording-contract tests for every current model-backed Agent role."""

import hashlib
import json
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import ClassVar

import pytest

from agentplanex.domains import (
    ActionOutput,
    Message,
    Milestone,
    MilestoneState,
    ProjectRuntimeContext,
    Stage,
    StageRun,
    StageRunStatus,
)
from agentplanex.infrastructure.codex import (
    CodexTurnRequest,
    CodexTurnResult,
    CodexTurnTransport,
)
from agentplanex.infrastructure.sqlite import SQLiteDatabase
from agentplanex.infrastructure.sqlite.repositories import (
    SQLiteProjectOwnerAgentRepository,
)
from agentplanex.project_owner_agent.exception import ReplyToHuman
from agentplanex.project_runtime import ProjectRuntime
from agentplanex.project_runtime.executions import create_project_executions
from agentplanex.services import project_owner as project_owner_service
from agentplanex.services.agent_collaboration import AgentCollaborationService
from agentplanex.services.agent_contracts import resolve_observation_skill
from agentplanex.services.delivery import MilestoneReviewRequest
from agentplanex.services.plan_hard_gate import CodexPlanHardGate
from agentplanex.services.planning import PlanReviewRequest
from agentplanex.services.stage_executor import CodexStageExecutor, StageExecutionRequest
from agentplanex.settings import (
    ModelSettings,
    ProjectOwnerAgentSettings,
    RuntimeSettings,
    Settings,
)


class _RecordingOwnerModel:
    queries: ClassVar[list[list[Message]]] = []

    def __init__(self, **_kwargs: object) -> None:
        pass

    def query(self, messages: list[Message]) -> Message:
        type(self).queries.append([dict(message) for message in messages])
        raise ReplyToHuman(
            content="Recorded Owner invocation.",
            response={"role": "assistant", "content": "Recorded Owner invocation."},
        )

    def format_observation_messages(
        self,
        message: Message,
        outputs: list[ActionOutput],
    ) -> list[Message]:
        raise AssertionError("The recording Owner does not call tools")


def _settings() -> Settings:
    return Settings(
        project_owner_agent=ProjectOwnerAgentSettings(
            model=ModelSettings(name="test-model")
        )
    )


def test_owner_invocation_identifies_role_activation_and_observation_entry(
    initialize_git_project: Callable[[], Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_path = initialize_git_project()
    _RecordingOwnerModel.queries = []
    monkeypatch.setattr(project_owner_service, "JBBModel", _RecordingOwnerModel)
    runtime = ProjectRuntime(
        project_path=project_path,
        settings=_settings(),
        approval_mode="yolo",
    )

    activation = runtime.submit_message("Clarify the current project work.")
    runtime.drive_next_activation()

    instructions = str(_RecordingOwnerModel.queries[-1][0]["content"])
    skill_path = resolve_observation_skill()
    assert "Project Owner" in instructions
    assert "project governance" in instructions
    assert "agentplanex-project-observe" in instructions
    assert skill_path.is_file()
    assert f'"observation_skill": "{skill_path}"' in instructions
    assert f'"project_root": "{project_path.resolve()}"' in instructions
    assert f'"triage_id": "{activation.triage_id}"' in instructions
    assert f'"activation_id": "{activation.activation_id}"' in instructions
    assert '"operation": "owner_activation:USER_INPUT"' in instructions


def test_existing_owner_prompt_is_upgraded_before_the_next_invocation(
    initialize_git_project: Callable[[], Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_path = initialize_git_project()
    _RecordingOwnerModel.queries = []
    monkeypatch.setattr(project_owner_service, "JBBModel", _RecordingOwnerModel)
    current_prompt = project_owner_service.DEFAULT_SYSTEM_PROMPT
    monkeypatch.setattr(
        project_owner_service,
        "DEFAULT_SYSTEM_PROMPT",
        project_owner_service.LEGACY_SYSTEM_PROMPT,
    )
    runtime = ProjectRuntime(
        project_path=project_path,
        settings=_settings(),
        approval_mode="yolo",
    )
    activation = runtime.submit_message("Use the upgraded Owner contract.")
    monkeypatch.setattr(
        project_owner_service,
        "DEFAULT_SYSTEM_PROMPT",
        current_prompt,
    )
    database = SQLiteDatabase.for_project(project_path)
    owners = SQLiteProjectOwnerAgentRepository()
    with database.read_only_connection() as connection:
        owner = owners.get_by_triage_id(connection, activation.triage_id)
    assert owner is not None
    assert owner.system_prompt == project_owner_service.LEGACY_SYSTEM_PROMPT

    runtime.drive_next_activation()

    instructions = str(_RecordingOwnerModel.queries[-1][0]["content"])
    assert instructions.startswith(project_owner_service.DEFAULT_SYSTEM_PROMPT)
    with database.read_only_connection() as connection:
        owner = owners.get_by_triage_id(connection, activation.triage_id)
    assert owner is not None
    assert owner.system_prompt == project_owner_service.DEFAULT_SYSTEM_PROMPT


def test_runtime_uses_packaged_observation_skill_independent_of_target_project(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    skill_path = resolve_observation_skill()

    assert skill_path == (
        Path(project_owner_service.__file__).parents[1]
        / "resources"
        / "skills"
        / "agentplanex-project-observe"
        / "SKILL.md"
    )
    repository_skill = (
        Path(__file__).parents[1]
        / ".codex"
        / "skills"
        / "agentplanex-project-observe"
        / "SKILL.md"
    )
    assert skill_path.read_bytes() == repository_skill.read_bytes()
    assert (skill_path.parent / "references" / "detail.md").read_bytes() == (
        repository_skill.parent / "references" / "detail.md"
    ).read_bytes()
    ProjectRuntime(
        project_path=project_path,
        settings=_settings(),
        approval_mode="yolo",
    )


def test_talk_to_agent_reanchors_planner_and_reviewer_to_runtime_context(
    initialize_git_project: Callable[[], Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_path = initialize_git_project()
    skill_path = resolve_observation_skill()
    requirement = project_path / "requirements.md"
    requirement_content = "# Requirements\n\nKeep Agent contracts explicit.\n"
    requirement.write_text(requirement_content, encoding="utf-8")
    requirement_sha = hashlib.sha256(requirement_content.encode("utf-8")).hexdigest()
    recorded: list[CodexTurnRequest] = []

    def record(
        _self: CodexTurnTransport,
        request: CodexTurnRequest,
    ) -> CodexTurnResult:
        recorded.append(request)
        if "Task Contract" in request.message:
            result_path = next(request.workspace.glob("outbox/*")) / "result.json"
            document = request.workspace / "documents" / "plan.md"
            document.write_text("# Recorded plan\n", encoding="utf-8")
            result_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "summary": "Recorded collaboration.",
                        "artifacts": [
                            {
                                "path": "documents/plan.md",
                                "media_type": "text/markdown",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
        return CodexTurnResult(
            thread_id=request.thread_id or "recorded-thread",
            turn_id="recorded-turn",
            status="completed",
            final_response='{"summary":"Recorded collaboration."}',
        )

    monkeypatch.setattr(CodexTurnTransport, "run", record)
    executions = create_project_executions(project_path, RuntimeSettings())
    context = ProjectRuntimeContext(
        triage_id="triage-contract",
        status="IN_PROGRESS",
        current_plan_commit_sha="plan-commit",
        current_snapshot_id="snapshot-1",
        current_run_id="run-1",
        current_milestone_key="milestone-1",
        current_stage_key="stage-1",
    )

    results = []
    for agent_id, kind, artifacts in (
        ("planner", "task", [{"uri": "project:///requirements.md"}]),
        ("reviewer", "message", []),
    ):
        result = executions.execute(
            context,
            {
                "tool": "talk_to_agent",
                "arguments": {
                    "agent_id": agent_id,
                    "kind": kind,
                    "message": "Inspect the supplied question.",
                    "artifacts": artifacts,
                },
            },
        )
        assert result.output["ok"] is True
        results.append(result)

    resumed = executions.execute(
        replace(
            context,
            current_snapshot_id="snapshot-2",
            current_stage_key="stage-2",
        ),
        {
            "tool": "talk_to_agent",
            "arguments": {
                "agent_id": "planner",
                "kind": "message",
                "message": "Continue against the new Runtime facts.",
                "conversation_id": results[0].output["conversation_id"],
                "artifacts": [],
            },
        },
    )
    assert resumed.output["ok"] is True

    planner, reviewer, resumed_planner = recorded
    assert "Project Planner" in planner.developer_instructions
    assert "must not approve" in planner.developer_instructions
    assert "Project Reviewer" in reviewer.developer_instructions
    assert "must not make the Owner's decision" in reviewer.developer_instructions
    assert '"operation": "project_planning:task"' in planner.message
    assert '"operation": "delegated_review:message"' in reviewer.message
    request_sha = hashlib.sha256(
        b"Inspect the supplied question."
    ).hexdigest()
    for request, role in zip(
        recorded[:2],
        ("planner", "reviewer"),
        strict=True,
    ):
        assert "agentplanex-project-observe" in request.message
        assert f'"observation_skill": "{skill_path}"' in request.message
        assert f'"project_root": "{project_path.resolve()}"' in request.message
        assert '"triage_id": "triage-contract"' in request.message
        assert f'"role": "{role}"' in request.message
        assert '"delegated_request_sha256":' in request.message
        assert f'"delegated_request_sha256": "{request_sha}"' in request.message
        assert '"snapshot_id": "snapshot-1"' in request.message
        assert '"stage_key": "stage-1"' in request.message
        assert request.output_schema is not None
        assert request.workspace.is_relative_to(
            project_path / ".agentplanex" / "agent-workspaces"
        )
    assert "Task Contract" in planner.message
    assert len(results[0].output["artifacts"]) == 1
    assert '"uri": "project:///requirements.md"' in planner.message
    assert f'"sha256": "{requirement_sha}"' in planner.message
    assert planner.mentions == (("artifact-1-requirements.md", requirement),)
    assert reviewer.mentions == ()
    assert resumed_planner.thread_id == "recorded-thread"
    assert '"snapshot_id": "snapshot-2"' in resumed_planner.message
    assert '"stage_key": "stage-2"' in resumed_planner.message


def test_hard_gates_record_distinct_fixed_subject_contracts(
    initialize_git_project: Callable[[], Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_path = initialize_git_project()
    skill_path = resolve_observation_skill()
    recorded: list[CodexTurnRequest] = []

    def record(
        _self: CodexTurnTransport,
        request: CodexTurnRequest,
    ) -> CodexTurnResult:
        recorded.append(request)
        result_path = next(request.workspace.glob("outbox/*")) / "result.json"
        (request.workspace / "documents" / "review.md").write_text(
            "# Recorded review\n", encoding="utf-8"
        )
        digest = request.message.split(
            "The Runtime-computed subject digest is: ", 1
        )[1].split("\n", 1)[0]
        result_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "subject_digest": digest,
                    "decision": "pass",
                    "summary": "Recorded gate.",
                    "required_changes": [],
                    "artifacts": [
                        {
                            "path": "documents/review.md",
                            "media_type": "text/markdown",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return CodexTurnResult(
            thread_id="fresh-gate-thread",
            turn_id="recorded-turn",
            status="completed",
            final_response='{"summary":"Recorded gate."}',
        )

    monkeypatch.setattr(CodexTurnTransport, "run", record)
    collaboration = AgentCollaborationService.from_settings(
        project_path, RuntimeSettings()
    )
    gate = CodexPlanHardGate(collaboration)
    specs = tuple(
        project_path / name
        for name in ("architecture.md", "requirements.md", "roadmap.md")
    )
    for spec in specs:
        spec.write_text(f"# {spec.stem}\n", encoding="utf-8")

    gate.review(
        PlanReviewRequest(
            triage_id="triage-gate",
            spec_documents=specs,
            subject_digest="plan-digest",
        )
    )
    gate.review_milestones(
        MilestoneReviewRequest(
            triage_id="triage-gate",
            plan_commit_sha="approved-plan",
            milestones=(
                Milestone(
                    key="m1",
                    objective="Establish contracts.",
                    state=MilestoneState.PENDING,
                    stages=(Stage(key="s1", objective="Record prompts."),),
                ),
            ),
            subject_digest="milestone-digest",
        )
    )

    plan_gate, milestone_gate = recorded
    assert plan_gate.thread_id is None
    assert milestone_gate.thread_id is None
    assert '"role": "plan_hard_gate"' in plan_gate.message
    assert '"subject_digest": "plan-digest"' in plan_gate.message
    assert '"role": "milestone_hard_gate"' in milestone_gate.message
    assert '"subject_digest": "milestone-digest"' in milestone_gate.message
    assert '"plan_commit_sha": "approved-plan"' in milestone_gate.message
    assert "protected Plan Hard Gate" in plan_gate.developer_instructions
    assert "protected Milestone Hard Gate" in milestone_gate.developer_instructions
    for request in recorded:
        assert "agentplanex-project-observe" in request.message
        assert f'"observation_skill": "{skill_path}"' in request.message
        assert "must not make the Owner's decision" in request.developer_instructions
        assert "fixed subject" in request.developer_instructions
        assert len(request.mentions) == (3 if request is plan_gate else 1)
        assert request.output_schema is not None


def test_stage_executor_records_one_fixed_stage_and_observation_boundary(
    initialize_git_project: Callable[[], Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_path = initialize_git_project()
    skill_path = resolve_observation_skill()
    recorded: list[CodexTurnRequest] = []

    def record(
        _self: CodexTurnTransport,
        request: CodexTurnRequest,
    ) -> CodexTurnResult:
        recorded.append(request)
        return CodexTurnResult(
            thread_id="fresh-stage-thread",
            turn_id="recorded-turn",
            status="completed",
            final_response='{"summary":"Recorded stage."}',
        )

    monkeypatch.setattr(CodexTurnTransport, "run", record)
    transport = CodexTurnTransport(None, None, 30.0, 65_536)
    worktree = project_path / ".agentplanex" / "worktrees" / "run-1"
    worktree.mkdir(parents=True)
    stage = Stage(key="s1", objective="Implement the fixed contract.")
    milestone = Milestone(
        key="m1",
        objective="Establish contracts.",
        state=MilestoneState.PENDING,
        stages=(stage,),
    )
    started_at = datetime.now(UTC)
    stage_run = StageRun(
        stage_run_id="stage-run-1",
        triage_id="triage-stage",
        run_id="run-1",
        snapshot_id="snapshot-1",
        milestone_key="m1",
        stage_key="s1",
        status=StageRunStatus.RUNNING,
        input_commit_sha="input-commit",
        output_commit_sha=None,
        failure=None,
        created_at=started_at,
        started_at=started_at,
        lease_expires_at=started_at + timedelta(minutes=5),
    )

    CodexStageExecutor(
        project_path,
        transport,
        resolve_observation_skill(),
    ).execute(
        StageExecutionRequest(
            stage_run=stage_run,
            milestone=milestone,
            stage=stage,
            worktree=worktree,
            delivery_document=worktree / ".agentplanex" / "delivery.md",
        )
    )

    request = recorded[0]
    assert request.thread_id is None
    assert "Stage Executor" in request.developer_instructions
    assert "must not re-plan" in request.developer_instructions
    assert "agentplanex-project-observe" in request.message
    assert f'"observation_skill": "{skill_path}"' in request.message
    assert f'"project_root": "{project_path.resolve()}"' in request.message
    assert '"role": "stage_executor"' in request.message
    assert '"stage_run_id": "stage-run-1"' in request.message
    assert '"input_commit_sha": "input-commit"' in request.message
    assert request.workspace == worktree
    assert request.mentions == ()
    assert request.output_schema is not None
