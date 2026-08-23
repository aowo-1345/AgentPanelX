"""Recording-contract tests for every current model-backed Agent role."""

import hashlib
import json
import re
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
from agentplanex.infrastructure.git_repository import GitRepository
from agentplanex.infrastructure.sqlite import SQLiteDatabase
from agentplanex.infrastructure.sqlite.repositories import (
    SQLiteMessageHistoryRepository,
    SQLiteProjectOwnerAgentRepository,
)
from agentplanex.project_owner_agent.contracts import ActionOutput, Message
from agentplanex.project_owner_agent.exception import ReplyToHuman
from agentplanex.project_owner_agent.models.responses import ResponsesRequest
from agentplanex.services._hard_gate import HardGateOperation
from agentplanex.services.agent_invocation import (
    resolve_observation_skill,
)
from agentplanex.services.delivery._milestone_hard_gate import MilestoneHardGate
from agentplanex.services.delivery._stage_executor import (
    CodexStageExecutor,
    StageExecutionRequest,
    _StageOperation,
)
from agentplanex.services.delivery.contracts import MilestoneReviewRequest
from agentplanex.services.delivery.models import (
    Milestone,
    MilestoneState,
    Stage,
    StageRun,
    StageRunStatus,
)
from agentplanex.services.external_agent_runtime import ExternalAgentRuntime
from agentplanex.services.external_agent_runtime._definitions import (
    build_agent_definition,
)
from agentplanex.services.planning._plan_hard_gate import PlanHardGate
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
                "project_owner": prompts.project_owner.model_copy(update={"role": owner_prompt})
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


def test_owner_requests_extend_the_persisted_session_without_an_invocation_envelope(
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
    assert "Project Owner" in instructions
    assert "three canonical project-root Specs" in instructions
    assert "task_distributor" in instructions
    assert "Once the Plan is approved" in _normalized(instructions)
    assert "before publishing the initial Milestone View" in _normalized(instructions)
    assert "After each accepted Candidate" in _normalized(instructions)
    assert "Do not repeat this consultation between Stages" in _normalized(instructions)
    assert "failure evidence invalidates the current decomposition" in _normalized(instructions)
    assert "one or more appropriate Tool Actions" in _normalized(instructions)
    assert "MULTIPLE tool calls in a single response" in _normalized(instructions)
    assert "tool calls are independent" in instructions
    assert query[-1] == {"role": "user", "content": "Clarify the current project work."}
    serialized_query = json.dumps(query, ensure_ascii=False)
    assert "AgentPlaneX invocation envelope" not in serialized_query
    assert "fixed_work_object" not in serialized_query
    assert all(
        identifier not in serialized_query
        for identifier in (
            activation.activation_id,
            activation.message_id,
            activation.triage_id,
        )
    )

    next_activation = runtime.submit_message("Continue with the same Owner contract.")
    runtime.drive_owner_model()
    next_query = _RecordingOwnerModel.queries[-1]

    assert next_query[: len(query)] == query
    assert next_query[-2:] == [
        {"role": "assistant", "content": "Recorded Owner invocation."},
        {"role": "user", "content": "Continue with the same Owner contract."},
    ]
    serialized_next_query = json.dumps(next_query, ensure_ascii=False)
    assert "AgentPlaneX invocation envelope" not in serialized_next_query
    assert "fixed_work_object" not in serialized_next_query
    assert all(
        identifier not in serialized_next_query
        for identifier in (
            next_activation.activation_id,
            next_activation.message_id,
            next_activation.triage_id,
        )
    )

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
        Path(__file__).parents[1] / ".codex" / "skills" / "agentplanex-project-observe" / "SKILL.md"
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
        *,
        on_thread_opened: object | None = None,
    ) -> CodexTurnResult:
        recorded.append(request)
        if on_thread_opened is not None:
            assert callable(on_thread_opened)
            on_thread_opened(request.thread_id or "recorded-thread")
        if "Task manifest" in request.message:
            result_path = (
                max(
                    (request.workspace / "outbox").iterdir(),
                    key=lambda path: path.stat().st_mtime_ns,
                )
                / "result.json"
            )
            document_name = (
                "milestone-plan.md"
                if "Task Distributor" in request.developer_instructions
                else "plan.md"
            )
            artifact = {
                "path": f"documents/{document_name}",
                "media_type": "text/markdown",
            }
            document = request.workspace / "documents" / document_name
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
                "artifacts": [],
            },
        },
    )
    assert resumed.output["ok"] is True

    second_review = executions.execute(
        context,
        {
            "tool": "talk_to_agent",
            "arguments": {
                "agent_id": "reviewer",
                "kind": "message",
                "message": "Review this independently.",
                "artifacts": [],
            },
        },
    )
    assert second_review.output["ok"] is True

    planner, task_distributor, reviewer, resumed_planner, reviewer_again = recorded
    assert planner.developer_instructions.startswith("You are the AgentPlaneX Planner")
    assert task_distributor.developer_instructions.startswith(
        "You are the AgentPlaneX Task Distributor"
    )
    assert task_distributor.workspace != planner.workspace
    assert task_distributor.thread_id is None
    assert "first unfinished Milestone" in _normalized(task_distributor.developer_instructions)
    assert "nested Task schema" in _normalized(task_distributor.developer_instructions)
    assert reviewer.developer_instructions.startswith("You are the AgentPlaneX Reviewer")
    for request in (planner, task_distributor, reviewer):
        assert request.skills == (("agentplanex-project-observe", skill_path),)
        assert str(skill_path) not in request.message
        assert '"snapshot_id": "snapshot-1"' in request.message
        assert '"stage_key": "stage-1"' in request.message
        assert request.output_schema is not None
        assert request.workspace.is_relative_to(project_path / ".agentplanex" / "agent-workspaces")
    assert len(results[0].output["artifacts"]) == 1
    assert len(results[1].output["artifacts"]) == 1
    assert "/artifacts/" in results[1].output["artifacts"][0]["uri"]
    assert results[1].output["artifacts"][0]["project_relative_path"].endswith("milestone-plan.md")
    assert '"uri": "project:///requirements.md"' in planner.message
    assert f'"sha256": "{requirement_sha}"' in planner.message
    assert len(planner.mentions) == 1
    assert planner.mentions[0][1] != requirement
    assert planner.mentions[0][1].read_text(encoding="utf-8") == requirement_content
    assert reviewer.mentions == ()
    assert resumed_planner.thread_id == "recorded-thread"
    assert '"snapshot_id": "snapshot-2"' in resumed_planner.message
    assert '"stage_key": "stage-2"' in resumed_planner.message
    assert reviewer.thread_id is None
    assert reviewer_again.thread_id is None
    assert reviewer_again.workspace != reviewer.workspace


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
        *,
        on_thread_opened: object | None = None,
    ) -> CodexTurnResult:
        recorded.append(request)
        if on_thread_opened is not None:
            assert callable(on_thread_opened)
            on_thread_opened("fresh-gate-thread")
        matched = re.search(r'"subject_digest":\s*"([^"]+)"', request.message)
        assert matched is not None
        result_path = (
            max(
                (request.workspace / "outbox").iterdir(),
                key=lambda path: path.stat().st_mtime_ns,
            )
            / "result.json"
        )
        (request.workspace / "documents" / "review.md").write_text(
            "# Recorded review\n", encoding="utf-8"
        )
        result_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "subject_digest": matched.group(1),
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
    runtime_settings = _settings().runtime
    codex_settings = runtime_settings.codex
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
    plan_definition = build_agent_definition(
        "plan_hard_gate",
        runtime_settings.external_agents["plan_hard_gate"],
    )
    milestone_definition = build_agent_definition(
        "milestone_hard_gate",
        runtime_settings.external_agents["milestone_hard_gate"],
    )
    runtime = ExternalAgentRuntime(
        workspaces=workspaces,
        transport=transport,
        definitions={
            "plan_hard_gate": plan_definition,
            "milestone_hard_gate": milestone_definition,
        },
        operations={
            ("plan_hard_gate", "plan_hard_gate_v1"): HardGateOperation("plan_hard_gate_v1"),
            (
                "milestone_hard_gate",
                "milestone_hard_gate_v1",
            ): HardGateOperation("milestone_hard_gate_v1"),
        },
    )
    plan_gate = PlanHardGate(runtime=runtime)
    milestone_gate = MilestoneHardGate(runtime=runtime)
    specs = tuple(
        project_path / name for name in ("architecture.md", "requirements.md", "roadmap.md")
    )
    for spec in specs:
        spec.write_text(f"# {spec.stem}\n", encoding="utf-8")

    plan_subject = PlanSubject(
        tuple(PlanDocument(name=spec.name, content=spec.read_bytes()) for spec in specs)
    )
    plan_request = PlanReviewRequest(triage_id="triage-gate", subject=plan_subject)
    plan_gate.review(plan_request)
    plan_gate.review(plan_request)
    milestone_gate.review(
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
    assert f'"subject_digest": "{plan_subject.digest}"' in plan_gate.message
    assert '"subject_digest": "milestone-digest"' in milestone_gate.message
    assert '"plan_commit_sha": "approved-plan"' in milestone_gate.message
    assert plan_gate.developer_instructions.startswith("You are the AgentPlaneX Plan Hard Gate")
    assert milestone_gate.developer_instructions.startswith(
        "You are the AgentPlaneX Milestone Hard Gate"
    )
    for request in recorded:
        assert request.skills == (("agentplanex-project-observe", skill_path),)
        assert str(skill_path) not in request.message
        assert len(request.mentions) == (3 if request is plan_gate else 1)
        assert request.output_schema is not None
        assert all(not path.is_relative_to(request.workspace) for _, path in request.mentions)
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
        *,
        on_thread_opened: object | None = None,
    ) -> CodexTurnResult:
        recorded.append(request)
        if on_thread_opened is not None:
            assert callable(on_thread_opened)
            on_thread_opened("fresh-stage-thread")
        return CodexTurnResult(
            thread_id="fresh-stage-thread",
            turn_id="recorded-turn",
            status="completed",
            final_response='{"summary":"Recorded stage."}',
        )

    monkeypatch.setattr(CodexTurnTransport, "run", record)
    transport = CodexTurnTransport(None, None, 30.0, 65_536)
    workspaces = AgentWorkspaceStore(project_path, 65_536, 262_144)
    worktree = GitRepository(project_path).delivery_worktree_path("run-1")
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

    runtime_settings = _settings().runtime
    stage_definition = build_agent_definition(
        "stage_executor",
        runtime_settings.external_agents["stage_executor"],
    )
    executor = CodexStageExecutor(
        runtime=ExternalAgentRuntime(
            workspaces=workspaces,
            transport=transport,
            definitions={"stage_executor": stage_definition},
            operations={("stage_executor", "stage_execution_v1"): _StageOperation()},
        ),
    )
    execution_request = StageExecutionRequest(
        stage_run=stage_run,
        milestone=milestone,
        stage=stage,
        worktree=worktree,
        delivery_document=(worktree / "docs" / "agentplanex" / "deliveries" / "run-1" / "s1.md"),
    )
    executor.execute(execution_request)
    executor.execute(execution_request)

    request = recorded[0]
    assert request.thread_id is None
    assert request.developer_instructions.startswith("You are the AgentPlaneX Stage Executor")
    assert request.skills == (("agentplanex-project-observe", skill_path),)
    assert str(skill_path) not in request.message
    assert '"stage_run_id": "stage-run-1"' in request.message
    assert '"input_commit_sha": "input-commit"' in request.message
    assert "Establish contracts." in request.message
    assert "Implement the fixed contract." in request.message
    assert request.workspace == worktree
    assert request.mentions == ()
    assert request.output_schema is not None
