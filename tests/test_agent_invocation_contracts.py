"""Recording-contract tests for every current model-backed Agent role."""

import hashlib
import json
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import ClassVar

import pytest

from agentplanex.bootstrap import create_project_runtime_control
from agentplanex.domains.execution_event import RuntimeContextChangeReason
from agentplanex.infrastructure.agent_workspace import AgentWorkspaceStore
from agentplanex.infrastructure.codex import (
    CodexTurnRequest,
    CodexTurnResult,
    CodexTurnTransport,
)
from agentplanex.infrastructure.sqlite import SQLiteDatabase
from agentplanex.infrastructure.sqlite.repositories import (
    SQLiteMessageHistoryRepository,
    SQLiteProjectOwnerAgentRepository,
)
from agentplanex.project_owner_agent.contracts import ActionOutput, Message
from agentplanex.project_owner_agent.exception import ReplyToHuman
from agentplanex.project_owner_agent.models.responses import ResponsesRequest
from agentplanex.services.agent_collaboration._catalog import AgentCatalog
from agentplanex.services.agent_collaboration._hard_gate import CodexHardGate
from agentplanex.services.agent_invocation import (
    AgentPromptCatalog,
    resolve_observation_skill,
)
from agentplanex.services.delivery._stage_executor import (
    CodexStageExecutor,
    StageExecutionRequest,
)
from agentplanex.services.delivery.contracts import MilestoneReviewRequest
from agentplanex.services.delivery.models import (
    Milestone,
    MilestoneState,
    Stage,
    StageRun,
    StageRunStatus,
)
from agentplanex.services.planning.contracts import PlanReviewRequest
from agentplanex.services.planning.models import PlanDocument, PlanSubject
from agentplanex.services.project_runtime_context import _owner as project_owner_service
from agentplanex.settings import (
    DEFAULT_SETTINGS_PATH,
    ModelSettings,
    ProjectOwnerAgentSettings,
    Settings,
    load_settings,
)
from tests.runtime_support import compose_test_executions


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


class _UnusedResponsesTransport:
    def create(self, _request: ResponsesRequest) -> object:
        raise AssertionError("This test replaces the Project Owner model")


_UNUSED_RESPONSES_TRANSPORT = _UnusedResponsesTransport()


def _settings(*, owner_prompt: str | None = None) -> Settings:
    configured = load_settings(DEFAULT_SETTINGS_PATH)
    prompts = configured.runtime.prompts
    if owner_prompt is not None:
        prompts = prompts.model_copy(
            update={
                "project_owner": prompts.project_owner.model_copy(
                    update={"role": owner_prompt}
                )
            }
        )
    return configured.model_copy(
        update={
            "project_owner_agent": ProjectOwnerAgentSettings(
                active_model="test",
                models={"test": ModelSettings(adapter="qwen", name="test-model")},
            ),
            "runtime": configured.runtime.model_copy(update={"prompts": prompts}),
        }
    )


def _invocation_envelope(message: str) -> dict[str, object]:
    marker = "AgentPlaneX invocation envelope (Runtime-provided identity):\n\n"
    start = message.index(marker) + len(marker)
    parsed, _ = json.JSONDecoder().raw_decode(message[start:])
    assert isinstance(parsed, dict)
    return parsed


def _normalized(value: str) -> str:
    return " ".join(value.split())


def test_owner_invocation_identifies_role_activation_and_observation_entry(
    initialize_git_project: Callable[[], Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_path = initialize_git_project()
    _RecordingOwnerModel.queries = []
    monkeypatch.setattr(project_owner_service, "ProjectOwnerModel", _RecordingOwnerModel)
    runtime = create_project_runtime_control(
        project_path=project_path,
        settings=_settings(),
        approval_mode="yolo",
        responses_transport=_UNUSED_RESPONSES_TRANSPORT,
    )
    runtime.initialize()

    activation = runtime.submit_message("Clarify the current project work.")
    runtime.drive_owner_model()

    query = _RecordingOwnerModel.queries[-1]
    instructions = str(query[0]["content"])
    invocation = str(query[-1]["content"])
    skill_path = resolve_observation_skill()
    assert "Project Owner" in instructions
    assert "three canonical project-root Specs" in instructions
    assert "task_distributor" in instructions
    assert "Once the Plan is approved" in _normalized(instructions)
    assert "before publishing the initial Milestone View" in _normalized(instructions)
    assert "After each accepted Candidate" in _normalized(instructions)
    assert "Do not repeat this consultation between Stages" in _normalized(
        instructions
    )
    assert "failure evidence invalidates the current decomposition" in _normalized(
        instructions
    )
    assert "one or more appropriate Tool Actions" in _normalized(instructions)
    assert "MULTIPLE tool calls in a single response" in _normalized(instructions)
    assert "tool calls are independent" in instructions
    assert skill_path.is_file()
    assert query[-1]["role"] == "developer"
    assert "agentplanex-project-observe" in invocation
    assert "CRAP" in invocation
    assert "Mutation testing" in _normalized(invocation)
    assert "Behavior-preserving refactoring" in invocation
    assert f'"observation_skill": "{skill_path}"' in invocation
    assert f'"project_root": "{project_path.resolve()}"' in invocation
    assert f'"triage_id": "{activation.triage_id}"' in invocation
    assert f'"activation_id": "{activation.activation_id}"' in invocation
    assert '"operation": "owner_activation:USER_INPUT"' in invocation

    next_activation = runtime.submit_message("Continue with the same Owner contract.")
    runtime.drive_owner_model()
    next_query = _RecordingOwnerModel.queries[-1]
    next_invocation = str(next_query[-1]["content"])

    assert next_query[0]["content"] == query[0]["content"]
    assert next_query[-1]["role"] == "developer"
    assert next_invocation != invocation
    assert f'"activation_id": "{next_activation.activation_id}"' in next_invocation
    assert f'"activation_id": "{activation.activation_id}"' not in next_invocation

    database = SQLiteDatabase.for_project(project_path)
    owners = SQLiteProjectOwnerAgentRepository()
    histories = SQLiteMessageHistoryRepository()
    with database.read_only_connection() as connection:
        owner = owners.get_by_triage_id(connection, activation.triage_id)
        assert owner is not None
        persisted = histories.list_by_session_id(
            connection,
            owner.project_owner_session_id,
        )
    assert "AgentPlaneX invocation envelope" not in json.dumps(
        [history.message for history in persisted],
        ensure_ascii=False,
    )


def test_existing_owner_prompt_remains_the_session_contract_after_restart(
    initialize_git_project: Callable[[], Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_path = initialize_git_project()
    _RecordingOwnerModel.queries = []
    monkeypatch.setattr(project_owner_service, "ProjectOwnerModel", _RecordingOwnerModel)
    old_prompt = "Configured Owner prompt before restart."
    current_prompt = "Configured Owner prompt after restart."
    runtime = create_project_runtime_control(
        project_path=project_path,
        settings=_settings(owner_prompt=old_prompt),
        approval_mode="yolo",
        responses_transport=_UNUSED_RESPONSES_TRANSPORT,
    )
    runtime.initialize()
    activation = runtime.submit_message("Use the persisted Owner contract.")
    runtime = create_project_runtime_control(
        project_path=project_path,
        settings=_settings(owner_prompt=current_prompt),
        approval_mode="yolo",
        responses_transport=_UNUSED_RESPONSES_TRANSPORT,
    )
    database = SQLiteDatabase.for_project(project_path)
    owners = SQLiteProjectOwnerAgentRepository()
    with database.read_only_connection() as connection:
        owner = owners.get_by_triage_id(connection, activation.triage_id)
    assert owner is not None
    assert owner.system_prompt == old_prompt

    runtime.drive_owner_model()

    query = _RecordingOwnerModel.queries[-1]
    instructions = str(query[0]["content"])
    assert instructions == old_prompt
    assert current_prompt not in instructions
    with database.read_only_connection() as connection:
        owner = owners.get_by_triage_id(connection, activation.triage_id)
    assert owner is not None
    assert owner.system_prompt == old_prompt


def test_runtime_uses_packaged_observation_skill_independent_of_target_project(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    skill_path = resolve_observation_skill()

    assert skill_path == (
        Path(project_owner_service.__file__).parents[2]
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
    create_project_runtime_control(
        project_path=project_path,
        settings=_settings(),
        approval_mode="yolo",
        responses_transport=_UNUSED_RESPONSES_TRANSPORT,
    )


def test_talk_to_agent_reanchors_configured_agents_to_runtime_context(
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
        if '"interaction": "task"' in request.message:
            envelope = _invocation_envelope(request.message)
            output = envelope["output_contract"]
            assert isinstance(output, dict)
            outbox = output["outbox"]
            assert isinstance(outbox, dict)
            schema = outbox["manifest_schema"]
            artifact = outbox["artifact_contract"]
            assert isinstance(schema, dict)
            assert isinstance(artifact, dict)
            assert set(schema["required"]) == {"version", "summary", "artifacts"}
            result_path = Path(str(outbox["result_path"]))
            document = request.workspace / "documents" / "plan.md"
            document.write_text("# Recorded plan\n", encoding="utf-8")
            result_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                            "summary": "Recorded collaboration.",
                            "artifacts": [artifact],
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
    composed = compose_test_executions(project_path, _settings().runtime)
    executions = composed.executions
    context = composed.context.transition(
        reason=RuntimeContextChangeReason.MILESTONES_UPDATED,
        mutate=lambda current: replace(
            current,
            status="IN_PROGRESS",
            current_plan_commit_sha="plan-commit",
            current_snapshot_id="snapshot-1",
            current_run_id="run-1",
            current_milestone_key="milestone-1",
            current_stage_key="stage-1",
        ),
    )

    results = []
    for agent_id, kind, artifacts in (
        ("planner", "task", [{"uri": "project:///requirements.md"}]),
        ("task_distributor", "task", []),
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
                        "conversation_id": None,
                        "artifacts": artifacts,
                },
            },
        )
        assert result.output["ok"] is True
        results.append(result)

    context = composed.context.transition(
        reason=RuntimeContextChangeReason.MILESTONES_UPDATED,
        mutate=lambda current: replace(
            current,
            current_snapshot_id="snapshot-2",
            current_stage_key="stage-2",
        ),
    )
    resumed = executions.execute(
        context,
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

    planner, task_distributor, reviewer, resumed_planner = recorded
    prompts = _settings().runtime.prompts
    assert planner.developer_instructions.startswith(prompts.planner.role.strip())
    assert task_distributor.developer_instructions.startswith(
        prompts.planner.role.strip()
    )
    assert task_distributor.workspace != planner.workspace
    assert task_distributor.thread_id is None
    assert "first unfinished Milestone" in task_distributor.developer_instructions
    assert "execution work" in task_distributor.developer_instructions
    assert "cleanup and hardening work" in _normalized(
        task_distributor.developer_instructions
    )
    assert "Stage count is unrestricted" in _normalized(
        task_distributor.developer_instructions
    )
    assert "Those are the only reasons for another Stage" in _normalized(
        task_distributor.developer_instructions
    )
    assert "Do not split work mechanically" in _normalized(
        task_distributor.developer_instructions
    )
    assert "nested Task schema" in _normalized(
        task_distributor.developer_instructions
    )
    assert "do not propose an evidence-only verification Stage" in _normalized(
        task_distributor.developer_instructions
    )
    assert "meaningful project or test changes" in _normalized(
        task_distributor.developer_instructions
    )
    assert reviewer.developer_instructions.startswith(prompts.reviewer.role.strip())
    assert '"operation": "project_planning:task"' in planner.message
    assert '"operation": "project_planning:task"' in task_distributor.message
    assert '"role": "planner"' in task_distributor.message
    assert "CRAP" in task_distributor.message
    assert "Mutation testing" in _normalized(task_distributor.message)
    assert "Behavior-preserving refactoring" in _normalized(
        task_distributor.message
    )
    assert '"operation": "delegated_review:message"' in reviewer.message
    request_sha = hashlib.sha256(
        b"Inspect the supplied question."
    ).hexdigest()
    for request, role in zip(
        (planner, task_distributor, reviewer),
        ("planner", "planner", "reviewer"),
        strict=True,
    ):
        assert "agentplanex-project-observe" in request.message
        assert "CRAP" in request.message
        assert "Mutation testing" in _normalized(request.message)
        assert f'"observation_skill": "{skill_path}"' in request.message
        assert f'"project_root": "{project_path.resolve()}"' in request.message
        assert f'"triage_id": "{context.triage_id}"' in request.message
        assert f'"role": "{role}"' in request.message
        assert '"delegated_request_sha256":' in request.message
        assert f'"delegated_request_sha256": "{request_sha}"' in request.message
        assert '"snapshot_id": "snapshot-1"' in request.message
        assert '"stage_key": "stage-1"' in request.message
        assert request.output_schema is not None
        assert request.workspace.is_relative_to(
            project_path / ".agentplanex" / "agent-workspaces"
        )
    assert '"interaction": "task"' in planner.message
    assert len(results[0].output["artifacts"]) == 1
    assert len(results[1].output["artifacts"]) == 1
    assert results[1].output["artifacts"][0]["project_relative_path"].endswith(
        "documents/plan.md"
    )
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
        envelope = _invocation_envelope(request.message)
        output = envelope["output_contract"]
        assert isinstance(output, dict)
        schema = output["manifest_schema"]
        subject = output["subject_contract"]
        artifact = output["artifact_contract"]
        assert isinstance(schema, dict)
        assert isinstance(subject, dict)
        assert isinstance(artifact, dict)
        assert set(schema["required"]) == {
            "version",
            "subject_digest",
            "decision",
            "summary",
            "required_changes",
            "artifacts",
        }
        result_path = Path(str(output["result_path"]))
        (request.workspace / "documents" / "review.md").write_text(
            "# Recorded review\n", encoding="utf-8"
        )
        digest = str(subject["subject_digest"])
        result_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "subject_digest": digest,
                    "decision": "pass",
                    "summary": "Recorded gate.",
                    "required_changes": [],
                        "artifacts": [artifact],
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
    runtime_settings = _settings().runtime
    codex_settings = runtime_settings.codex
    catalog = AgentCatalog(runtime_settings)
    workspaces = AgentWorkspaceStore(
        project_path,
        codex_settings.response_limit,
        codex_settings.artifact_limit,
    )
    transport = CodexTurnTransport(
        codex_settings.executable,
        codex_settings.model,
        codex_settings.timeout_seconds,
        codex_settings.response_limit,
        codex_settings.network_access,
    )
    prompts = AgentPromptCatalog(runtime_settings.prompts)
    gate = CodexHardGate(
        reviewer=catalog.get(catalog.hard_gate_reviewer_id),
        workspaces=workspaces,
        transport=transport,
        observation_skill=resolve_observation_skill(),
        prompts=prompts,
    )
    specs = tuple(
        project_path / name
        for name in ("architecture.md", "requirements.md", "roadmap.md")
    )
    for spec in specs:
        spec.write_text(f"# {spec.stem}\n", encoding="utf-8")

    plan_subject = PlanSubject(
        tuple(
            PlanDocument(name=spec.name, content=spec.read_bytes())
            for spec in specs
        )
    )
    gate.review_plan(
        PlanReviewRequest(triage_id="triage-gate", subject=plan_subject)
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
    assert f'"subject_digest": "{plan_subject.digest}"' in plan_gate.message
    assert '"role": "milestone_hard_gate"' in milestone_gate.message
    assert '"subject_digest": "milestone-digest"' in milestone_gate.message
    assert '"plan_commit_sha": "approved-plan"' in milestone_gate.message
    prompts = _settings().runtime.prompts
    assert plan_gate.developer_instructions.startswith(
        prompts.plan_hard_gate.role.strip()
    )
    assert milestone_gate.developer_instructions.startswith(
        prompts.milestone_hard_gate.role.strip()
    )
    for request in recorded:
        assert "agentplanex-project-observe" in request.message
        assert "CRAP" in request.message
        assert "Mutation testing" in _normalized(request.message)
        assert f'"observation_skill": "{skill_path}"' in request.message
        assert len(request.mentions) == (3 if request is plan_gate else 1)
        assert request.output_schema is not None
    assert tuple(path.read_bytes() for _, path in plan_gate.mentions) == tuple(
        document.content for document in plan_subject.documents
    )


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
        AgentPromptCatalog(_settings().runtime.prompts),
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
    prompts = _settings().runtime.prompts
    assert request.developer_instructions == prompts.stage_executor.role.strip()
    assert "Execute exactly the fixed Stage" in request.developer_instructions
    instructions = _normalized(request.developer_instructions)
    assert (
        "behavior-preserving cleanup, testing, hardening, or verification expected to leave"
        in instructions
    )
    assert "meaningful project or test changes" in instructions
    assert "agentplanex-project-observe" in request.message
    assert "CRAP" in request.message
    assert "Mutation testing" in _normalized(request.message)
    assert f'"observation_skill": "{skill_path}"' in request.message
    assert f'"project_root": "{project_path.resolve()}"' in request.message
    assert '"role": "stage_executor"' in request.message
    assert '"stage_run_id": "stage-run-1"' in request.message
    assert '"input_commit_sha": "input-commit"' in request.message
    assert "Establish contracts." in request.message
    assert "Implement the fixed contract." in request.message
    assert request.workspace == worktree
    assert request.mentions == ()
    assert request.output_schema is not None
