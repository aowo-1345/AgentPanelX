"""Single-Feature ProjectRuntime automatic-loop behavior."""

import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest

from agentplanex.domains import (
    OwnerActivation,
    ProjectOwnerTaskType,
)
from agentplanex.infrastructure.git_repository import GitRepository
from agentplanex.infrastructure.sqlite import SQLiteDatabase
from agentplanex.infrastructure.sqlite.repositories import (
    SQLiteMessageHistoryRepository,
    SQLiteOwnerActivationRepository,
    SQLiteProjectOwnerAgentRepository,
)
from agentplanex.project_owner_agent.exception import ModelGatewayError
from agentplanex.project_owner_agent.models.responses import (
    ResponsesRequest,
    ResponsesTransport,
)
from agentplanex.project_runtime import ProjectRuntime
from agentplanex.project_runtime.errors import FeatureBusyError
from agentplanex.services.delivery import delivery_candidate_ref
from agentplanex.services.project_control import ProjectControlQuery
from agentplanex.services.project_workspace import ProjectWorkspaceQuery
from agentplanex.services.stage_executor import StageExecutionRequest
from agentplanex.settings import DEFAULT_SETTINGS_PATH, Settings, load_settings


class _ReplyingOwner(ResponsesTransport):
    """A deterministic remote-model adapter that always replies to the user."""

    def create(self, _request: ResponsesRequest) -> object:
        return _text_response("Owner reached a human waiting point.")


class _FailingOwner(ResponsesTransport):
    """A deterministic remote-model adapter that fails outside Agent control flow."""

    def create(self, _request: ResponsesRequest) -> object:
        raise ModelGatewayError("owner gateway exploded")


class _UnexpectedOwner(ResponsesTransport):
    """Fail loudly if a human-owned waiting point is driven by the model."""

    def create(self, _request: ResponsesRequest) -> object:
        raise AssertionError("Owner model must not run at this waiting point")


class _MalformedOwner(ResponsesTransport):
    def create(self, _request: ResponsesRequest) -> object:
        return {"object": "response", "output": []}


class _EndlessToolOwner(ResponsesTransport):
    def create(self, _request: ResponsesRequest) -> object:
        return {
            "object": "response",
            "output": [
                {
                    "type": "function_call",
                    "name": "bash",
                    "call_id": "step-limit-tool",
                    "arguments": json.dumps({"command": "printf controlled"}),
                }
            ],
        }


class _BlockingOwner(ResponsesTransport):
    """Expose a real RUNNING Activation until the test simulates interruption."""

    def __init__(self) -> None:
        self.entered = Event()
        self.release = Event()

    def create(self, _request: ResponsesRequest) -> object:
        self.entered.set()
        if not self.release.wait(timeout=5):
            raise TimeoutError("test did not release the blocked Owner")
        raise RuntimeError("Owner process continued after simulated interruption")


class _SuccessfulStageExecutor:
    """A local Stage adapter that produces the declared delivery artifacts."""

    executed_stage_keys: list[str]

    def __init__(self) -> None:
        self.executed_stage_keys = []

    def execute(self, request: StageExecutionRequest) -> None:
        self.executed_stage_keys.append(request.stage.key)
        request.delivery_document.parent.mkdir(parents=True, exist_ok=True)
        request.delivery_document.write_text(
            f"# {request.stage.key}\n\nDeterministic delivery evidence.\n",
            encoding="utf-8",
        )
        implementation = request.worktree / "src" / f"{request.stage.key}.txt"
        implementation.parent.mkdir(parents=True, exist_ok=True)
        implementation.write_text(
            f"implemented {request.stage.key}\n",
            encoding="utf-8",
        )


class _FailingStageExecutor:
    def execute(self, _request: StageExecutionRequest) -> None:
        raise RuntimeError("unexpected deterministic Stage failure")


class _BlockingStageExecutor:
    """Expose a real RUNNING StageRun until the test simulates interruption."""

    def __init__(self) -> None:
        self.entered = Event()
        self.release = Event()

    def execute(self, _request: StageExecutionRequest) -> None:
        self.entered.set()
        if not self.release.wait(timeout=5):
            raise TimeoutError("test did not release the blocked Stage")
        raise RuntimeError("Stage process continued after simulated interruption")


def _text_response(text: str) -> object:
    return {
        "object": "response",
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text}],
            }
        ],
    }


def _settings(
    *,
    step_limit: int | None = None,
    max_consecutive_format_errors: int | None = None,
) -> Settings:
    configured = load_settings(DEFAULT_SETTINGS_PATH)
    updates: dict[str, int] = {}
    if step_limit is not None:
        updates["step_limit"] = step_limit
    if max_consecutive_format_errors is not None:
        updates["max_consecutive_format_errors"] = max_consecutive_format_errors
    if not updates:
        return configured
    return configured.model_copy(
        update={
            "project_owner_agent": configured.project_owner_agent.model_copy(
                update=updates
            )
        }
    )


def _write_plan(project_path: Path) -> None:
    for name in ("architecture.md", "requirements.md", "roadmap.md"):
        (project_path / name).write_text(f"# {name}\n", encoding="utf-8")


def _queue_first_run(runtime: ProjectRuntime, project_path: Path) -> None:
    _write_plan(project_path)
    requested = runtime.execute_action(
        {
            "tool": "request_plan_approval",
            "call_id": "request-plan",
            "arguments": {},
        }
    )
    assert requested.exit is not None
    assert requested.exit.status.value == "PlanApprovalRequested"
    runtime.approve_plan()
    runtime.drive_until_waiting()
    published = runtime.execute_action(
        {
            "tool": "update_milestones",
            "call_id": "publish-milestones",
            "arguments": {
                "reason": "Exercise the complete automatic Stage loop.",
                "milestones": [
                    {
                        "key": "milestone-1",
                        "objective": "Produce one reviewable Candidate.",
                        "state": "pending",
                        "stages": [
                            {"key": "stage-1", "objective": "Implement part one."},
                            {"key": "stage-2", "objective": "Implement part two."},
                        ],
                    }
                ],
            },
        }
    )
    assert published.output["accepted"] is True
    queued = runtime.execute_action(
        {
            "tool": "run_next_milestone",
            "call_id": "queue-first-run",
            "arguments": {},
        }
    )
    assert queued.exit is not None
    assert queued.exit.status.value == "FirstRunApprovalRequested"
    runtime.start_first_run()


def test_drive_until_waiting_consumes_message_and_returns_owner_reply(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    runtime = ProjectRuntime(
        project_path=project_path,
        settings=_settings(),
        approval_mode="yolo",
        responses_transport=_ReplyingOwner(),
    )
    runtime.initialize()
    runtime.begin_feature()
    activation = runtime.submit_message("Explain the next implementation step.")

    context = runtime.drive_until_waiting()

    assert context.triage_id == activation.triage_id
    assert context.status == "TODO"
    workspace = runtime.project_workspace_view(activation.triage_id)
    assert workspace.owner_activation is None
    assert [(message.role, message.content) for message in workspace.conversation] == [
        ("user", "Explain the next implementation step."),
        ("assistant", "Owner reached a human waiting point."),
    ]


def test_drive_until_waiting_blocks_runtime_when_owner_fails(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    runtime = ProjectRuntime(
        project_path=project_path,
        settings=_settings(),
        approval_mode="yolo",
        responses_transport=_FailingOwner(),
    )
    runtime.initialize()
    runtime.begin_feature()
    runtime.submit_message("Trigger a deterministic Owner failure.")

    context = runtime.drive_until_waiting()

    assert context.status == "BLOCKED"
    workspace = runtime.project_workspace_view(context.triage_id)
    assert workspace.owner_activation is None
    assert [(message.role, message.content) for message in workspace.conversation] == [
        ("user", "Trigger a deterministic Owner failure."),
        ("status", "Project Owner failed: ModelGatewayError: owner gateway exploded"),
    ]


@pytest.mark.parametrize(
    ("transport", "settings", "expected_failure"),
    [
        (
            _MalformedOwner(),
            _settings(max_consecutive_format_errors=2),
            "RepeatedFormatError",
        ),
        (
            _EndlessToolOwner(),
            _settings(step_limit=1),
            "StepLimitExceeded",
        ),
    ],
)
def test_structured_owner_failures_block_runtime(
    initialize_git_project: Callable[[], Path],
    transport: ResponsesTransport,
    settings: Settings,
    expected_failure: str,
) -> None:
    project_path = initialize_git_project()
    runtime = ProjectRuntime(
        project_path=project_path,
        settings=settings,
        approval_mode="yolo",
        responses_transport=transport,
    )
    runtime.initialize()
    runtime.begin_feature()
    runtime.submit_message("Reach a structured Owner failure.")

    context = runtime.drive_until_waiting()

    assert context.status == "BLOCKED"
    failure_messages = [
        message.content
        for message in runtime.project_workspace_view(context.triage_id).conversation
        if message.role == "status"
    ]
    assert len(failure_messages) == 1
    assert expected_failure in failure_messages[0]


def test_drive_until_waiting_stops_at_plan_approval(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    runtime = ProjectRuntime(
        project_path=project_path,
        settings=_settings(),
        approval_mode="yolo",
        responses_transport=_UnexpectedOwner(),
    )
    runtime.initialize()
    runtime.begin_feature()
    _write_plan(project_path)
    runtime.execute_action(
        {
            "tool": "request_plan_approval",
            "call_id": "wait-for-plan-approval",
            "arguments": {},
        }
    )

    context = runtime.drive_until_waiting()

    assert context.status == "TODO"
    assert context.pending_action == "PLAN_APPROVAL"


def test_message_does_not_clear_runtime_execution_block(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    runtime = ProjectRuntime(
        project_path=project_path,
        settings=_settings(),
        approval_mode="yolo",
        responses_transport=_FailingOwner(),
    )
    runtime.initialize()
    runtime.begin_feature()
    runtime.submit_message("Fail the first activation.")
    assert runtime.drive_until_waiting().status == "BLOCKED"

    pending = runtime.submit_message("Record follow-up without resuming work.")
    context = runtime.drive_until_waiting()

    assert context.status == "BLOCKED"
    workspace = runtime.project_workspace_view(context.triage_id)
    assert workspace.owner_activation == pending
    assert workspace.owner_activation.status.value == "PENDING"


def test_drive_until_waiting_leaves_tool_owned_activation_for_human_control(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    runtime = ProjectRuntime(
        project_path=project_path,
        settings=_settings(),
        approval_mode="yolo",
        responses_transport=_UnexpectedOwner(),
    )
    runtime.initialize()
    runtime.begin_feature()
    runtime.submit_message("Run one manually supplied step.")
    step = runtime.drive_activation_tool(
        {
            "tool": "bash",
            "call_id": "manual-step",
            "arguments": {"command": "printf controlled"},
        }
    )
    assert step.activation.status.value == "PENDING"
    assert step.activation.driver_mode is not None
    assert step.activation.driver_mode.value == "TOOL"

    context = runtime.drive_until_waiting()

    assert context.status == "TODO"
    workspace = runtime.project_workspace_view(context.triage_id)
    assert workspace.owner_activation == step.activation

    assert runtime.fail_interrupted_work() is True
    assert runtime.initialize().status == "BLOCKED"
    assert runtime.project_workspace_view(context.triage_id).owner_activation is None


def test_manual_activation_failure_blocks_runtime(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    runtime = ProjectRuntime(
        project_path=project_path,
        settings=_settings(),
        approval_mode="yolo",
        responses_transport=_UnexpectedOwner(),
    )
    runtime.initialize()
    runtime.begin_feature()
    runtime.submit_message("Fail this manual activation.")

    failed = runtime.fail_activation("Developer stopped the manual drive.")

    assert failed.activation.status.value == "FAILED"
    assert runtime.initialize().status == "BLOCKED"


def test_drive_until_waiting_runs_all_stages_then_delivers_candidate_to_owner(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    executor = _SuccessfulStageExecutor()
    runtime = ProjectRuntime(
        project_path=project_path,
        settings=_settings(),
        approval_mode="yolo",
        responses_transport=_ReplyingOwner(),
        stage_executor=executor,
    )
    runtime.initialize()
    runtime.begin_feature()
    _queue_first_run(runtime, project_path)

    context = runtime.drive_until_waiting()

    assert executor.executed_stage_keys == ["stage-1", "stage-2"]
    assert context.status == "IN_PROGRESS"
    assert context.current_candidate_commit_sha is not None
    control = runtime.project_control_view()
    assert [stage.status.value for stage in control.stage_runs] == [
        "SUCCEEDED",
        "SUCCEEDED",
    ]
    assert control.owner_activation is None
    assert runtime.project_workspace_view(context.triage_id).conversation[-1].content == (
        "Owner reached a human waiting point."
    )


def test_feature_runs_from_user_message_through_owner_tools_to_done(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    runtime = ProjectRuntime(
        project_path=project_path,
        settings=_settings(),
        approval_mode="yolo",
        responses_transport=_UnexpectedOwner(),
        stage_executor=_SuccessfulStageExecutor(),
    )
    initial = runtime.initialize()
    runtime.begin_feature()
    _write_plan(project_path)

    user_activation = runtime.submit_message("Deliver the complete Feature.")
    requested = runtime.drive_activation_tool(
        {
            "tool": "request_plan_approval",
            "call_id": "owner-request-plan",
            "arguments": {},
        }
    )
    assert requested.activation.activation_id == user_activation.activation_id
    assert requested.activation.status.value == "COMPLETED"
    plan_decision = runtime.approve_plan()

    published = runtime.drive_activation_tool(
        {
            "tool": "update_milestones",
            "call_id": "owner-publish-milestones",
            "arguments": {
                "reason": "Deliver one deterministic Milestone.",
                "milestones": [
                    {
                        "key": "milestone-1",
                        "objective": "Produce the complete Feature Candidate.",
                        "state": "pending",
                        "stages": [
                            {"key": "stage-1", "objective": "Implement the Feature."}
                        ],
                    }
                ],
            },
        }
    )
    assert published.activation.activation_id == plan_decision.activation.activation_id
    queued = runtime.drive_activation_tool(
        {
            "tool": "run_next_milestone",
            "call_id": "owner-request-first-run",
            "arguments": {},
        }
    )
    assert queued.activation.status.value == "COMPLETED"
    started = runtime.start_first_run()
    candidate = runtime.drive_delivery()
    assert candidate.outcome == "candidate_ready"
    assert candidate.activation is not None

    accepted = runtime.drive_activation_tool(
        {
            "tool": "decide_milestone_candidate",
            "call_id": "owner-accept-candidate",
            "arguments": {
                "decision": "accept",
                "reason": "Git and Stage evidence match the approved Milestone.",
            },
        }
    )

    state = runtime.state()
    assert accepted.activation.activation_id == candidate.activation.activation_id
    assert accepted.activation.status.value == "COMPLETED"
    assert state.status == "DONE"
    assert state.current_candidate_commit_sha is None
    control = runtime.project_control_view()
    assert [stage.status.value for stage in control.stage_runs] == ["SUCCEEDED"]
    output_commit = control.stage_runs[0].output_commit_sha
    assert output_commit is not None
    git = GitRepository(project_path)
    assert git.head_sha() == output_commit
    assert git.resolve_ref(delivery_candidate_ref(started.stage_run.run_id)) == output_commit
    assert (project_path / "src" / "stage-1.txt").read_text(encoding="utf-8") == (
        "implemented stage-1\n"
    )

    database = SQLiteDatabase.for_project(project_path)
    with database.read_only_connection() as connection:
        owner = SQLiteProjectOwnerAgentRepository().get_by_triage_id(
            connection,
            initial.triage_id,
        )
        activations = SQLiteOwnerActivationRepository().list_by_triage_id(
            connection,
            initial.triage_id,
        )
        assert owner is not None
        messages = SQLiteMessageHistoryRepository().list_by_session_id(
            connection,
            owner.project_owner_session_id,
        )
    assert [activation.task_type for activation in activations] == [
        ProjectOwnerTaskType.USER_INPUT,
        ProjectOwnerTaskType.PLAN_DECISION,
        ProjectOwnerTaskType.EXECUTION_RESULT,
    ]
    assert all(activation.status.value == "COMPLETED" for activation in activations)
    persisted_messages = tuple(
        message for history in messages for message in history.message
    )
    assert any(
        message.get("role") == "user"
        and message.get("content") == "Deliver the complete Feature."
        for message in persisted_messages
    )
    assert any(message.get("type") == "function_call" for message in persisted_messages)
    event_types = [event.event_type.value for event in control.timeline]
    assert event_types.index("CANDIDATE_READY") < event_types.index(
        "TRIAGE_DEVELOPMENT_COMPLETED"
    )
    assert "CANDIDATE_ACCEPTED" in event_types


def test_drive_until_waiting_returns_immediately_when_runtime_is_done(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    executor = _SuccessfulStageExecutor()
    runtime = ProjectRuntime(
        project_path=project_path,
        settings=_settings(),
        approval_mode="yolo",
        responses_transport=_ReplyingOwner(),
        stage_executor=executor,
    )
    runtime.initialize()
    runtime.begin_feature()
    _queue_first_run(runtime, project_path)
    runtime.drive_until_waiting()
    decision = runtime.execute_action(
        {
            "tool": "decide_milestone_candidate",
            "call_id": "accept-only-candidate",
            "arguments": {
                "decision": "accept",
                "reason": "The deterministic Candidate satisfies the Milestone.",
            },
        }
    )
    assert decision.exit is not None
    assert decision.exit.status.value == "TriageDevelopmentCompleted"
    executed_before_wait = list(executor.executed_stage_keys)

    context = runtime.drive_until_waiting()

    assert context.status == "DONE"
    assert executor.executed_stage_keys == executed_before_wait


def test_stage_failure_blocks_without_feedback_and_retries_only_by_owner_action(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    runtime = ProjectRuntime(
        project_path=project_path,
        settings=_settings(),
        approval_mode="yolo",
        responses_transport=_ReplyingOwner(),
        stage_executor=_FailingStageExecutor(),
    )
    runtime.initialize()
    runtime.begin_feature()
    _queue_first_run(runtime, project_path)

    context = runtime.drive_until_waiting()

    assert context.status == "BLOCKED"
    control = runtime.project_control_view()
    assert [stage.status.value for stage in control.stage_runs] == ["FAILED"]
    assert control.owner_activation is None
    conversation = runtime.project_workspace_view(context.triage_id).conversation
    assert all("Stage execution failed" not in item.content for item in conversation)

    database = SQLiteDatabase.for_project(project_path)
    with database.read_only_connection() as connection:
        owner = SQLiteProjectOwnerAgentRepository().get_by_triage_id(
            connection,
            context.triage_id,
        )
    assert owner is not None
    owner_session_id = owner.project_owner_session_id
    follow_up = runtime.submit_message("Retry the failed Milestone deliberately.")
    waiting = runtime.drive_until_waiting()
    assert waiting.status == "BLOCKED"
    assert runtime.project_control_view().owner_activation == follow_up

    retried = runtime.drive_activation_tool(
        {
            "tool": "run_next_milestone",
            "call_id": "retry-blocked-milestone",
            "arguments": {},
        }
    )

    assert retried.activation.activation_id == follow_up.activation_id
    assert retried.activation.status.value == "COMPLETED"
    assert retried.exit is not None
    assert retried.exit.status.value == "MilestoneRunQueued"
    resumed = runtime.initialize()
    assert resumed.status == "IN_PROGRESS"
    with database.read_only_connection() as connection:
        restored_owner = SQLiteProjectOwnerAgentRepository().get_by_triage_id(
            connection,
            resumed.triage_id,
        )
    assert restored_owner is not None
    assert restored_owner.project_owner_session_id == owner_session_id
    assert [stage.status.value for stage in runtime.project_control_view().stage_runs] == [
        "FAILED",
        "QUEUED",
    ]


def test_final_stage_handoff_failure_rolls_back_candidate_and_timeline(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    runtime = ProjectRuntime(
        project_path=project_path,
        settings=_settings(),
        approval_mode="yolo",
        responses_transport=_ReplyingOwner(),
        stage_executor=_SuccessfulStageExecutor(),
    )
    runtime.initialize()
    runtime.begin_feature()
    _queue_first_run(runtime, project_path)
    assert runtime.drive_delivery().outcome == "stage_succeeded"

    database = SQLiteDatabase.for_project(project_path)
    with database.transaction() as connection:
        activation_count_before = connection.execute(
            "SELECT COUNT(*) FROM owner_activation"
        ).fetchone()[0]
        connection.execute(
            """
            CREATE TRIGGER reject_candidate_activation
            BEFORE INSERT ON owner_activation
            BEGIN
                SELECT RAISE(ABORT, 'forced candidate handoff rollback');
            END
            """
        )

    result = runtime.drive_delivery()

    assert result.outcome == "stage_failed"
    state = runtime.state()
    assert state.status == "BLOCKED"
    assert state.current_candidate_commit_sha is None
    control = runtime.project_control_view()
    assert control.owner_activation is None
    assert [stage.status.value for stage in control.stage_runs] == [
        "SUCCEEDED",
        "FAILED",
    ]
    assert "CANDIDATE_READY" not in {
        event.event_type.value for event in control.timeline
    }
    with database.read_only_connection() as connection:
        activation_count = connection.execute(
            "SELECT COUNT(*) FROM owner_activation"
        ).fetchone()[0]
    assert activation_count == activation_count_before


def test_drive_until_waiting_rejects_two_simultaneously_runnable_work_items(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    runtime = ProjectRuntime(
        project_path=project_path,
        settings=_settings(),
        approval_mode="yolo",
        responses_transport=_ReplyingOwner(),
        stage_executor=_SuccessfulStageExecutor(),
    )
    initialized = runtime.initialize()
    runtime.begin_feature()
    _queue_first_run(runtime, project_path)
    database = SQLiteDatabase.for_project(project_path)
    with database.transaction() as connection:
        SQLiteOwnerActivationRepository().insert(
            connection,
            OwnerActivation(
                activation_id="corrupt-parallel-activation",
                triage_id=initialized.triage_id,
                task_type=ProjectOwnerTaskType.USER_INPUT,
                message_id="corrupt-parallel-message",
            ),
        )

    with pytest.raises(RuntimeError, match="both runnable"):
        runtime.drive_until_waiting()


def test_fail_interrupted_work_terminalizes_pending_activation_without_model(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    runtime = ProjectRuntime(
        project_path=project_path,
        settings=_settings(),
        approval_mode="yolo",
        responses_transport=_UnexpectedOwner(),
    )
    runtime.initialize()
    runtime.begin_feature()
    pending = runtime.submit_message(
        "This accepted work was interrupted before claim."
    )

    assert runtime.fail_interrupted_work() is True
    assert runtime.fail_interrupted_work() is False

    context = runtime.initialize()
    assert context.status == "BLOCKED"
    workspace = runtime.project_workspace_view(context.triage_id)
    assert workspace.owner_activation is None
    assert any(
        message.role == "status" and "interrupted" in message.content.lower()
        for message in workspace.conversation
    )
    with SQLiteDatabase.for_project(project_path).connection() as connection:
        failed = SQLiteOwnerActivationRepository().get(
            connection,
            pending.activation_id,
        )
    assert failed is not None
    assert failed.started_at is None
    event = next(
        event
        for event in runtime.project_control_view().timeline
        if event.event_type.value == "OWNER_ACTIVATION_FAILED"
    )
    assert event.payload["activation_id"] == pending.activation_id
    assert event.payload["started"] is False
    assert event.payload["interrupted"] is True


def test_fail_interrupted_work_terminalizes_queued_stage_and_preserves_cursor(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    runtime = ProjectRuntime(
        project_path=project_path,
        settings=_settings(),
        approval_mode="yolo",
        responses_transport=_ReplyingOwner(),
        stage_executor=_SuccessfulStageExecutor(),
    )
    runtime.initialize()
    runtime.begin_feature()
    _queue_first_run(runtime, project_path)
    before = runtime.initialize()

    assert runtime.fail_interrupted_work() is True

    after = runtime.initialize()
    assert after.status == "BLOCKED"
    assert after.current_snapshot_id == before.current_snapshot_id
    assert after.current_run_id == before.current_run_id
    assert after.current_milestone_key == before.current_milestone_key
    assert after.current_stage_key == before.current_stage_key
    control = runtime.project_control_view()
    assert [stage.status.value for stage in control.stage_runs] == ["FAILED"]
    assert control.stage_runs[0].started_at is None
    assert control.owner_activation is None
    event = next(
        event
        for event in control.timeline
        if event.event_type.value == "STAGE_RUN_FAILED"
        and event.payload.get("interrupted") is True
    )
    assert event.payload["stage_run_id"] == control.stage_runs[0].stage_run_id
    assert event.payload["started"] is False


def test_running_activation_rejects_concurrent_interruption_recovery(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    owner = _BlockingOwner()
    runtime = ProjectRuntime(
        project_path=project_path,
        settings=_settings(),
        approval_mode="yolo",
        responses_transport=owner,
    )
    runtime.initialize()
    runtime.begin_feature()
    runtime.submit_message("This activation was claimed before interruption.")
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(runtime.drive_until_waiting)
        assert owner.entered.wait(timeout=5)
        try:
            with pytest.raises(FeatureBusyError):
                runtime.fail_interrupted_work()
            with pytest.raises(FeatureBusyError):
                runtime.initialize()
        finally:
            owner.release.set()
        assert future.result(timeout=5).status == "BLOCKED"


def test_running_stage_rejects_concurrent_interruption_recovery(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    stage_executor = _BlockingStageExecutor()
    runtime = ProjectRuntime(
        project_path=project_path,
        settings=_settings(),
        approval_mode="yolo",
        responses_transport=_ReplyingOwner(),
        stage_executor=stage_executor,
    )
    state = runtime.initialize()
    runtime.begin_feature()
    _queue_first_run(runtime, project_path)
    database = SQLiteDatabase.for_project(project_path)
    git = GitRepository(project_path)
    control_query = ProjectControlQuery(database=database, git=git)
    workspace_query = ProjectWorkspaceQuery(database=database, git=git)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(runtime.drive_until_waiting)
        assert stage_executor.entered.wait(timeout=5)
        try:
            control = control_query.get_current()
            workspace = workspace_query.get(state.triage_id)
            assert control.state.status == "IN_PROGRESS"
            assert control.stage_runs[-1].status.value == "RUNNING"
            assert workspace.state == control.state
            with pytest.raises(FeatureBusyError):
                runtime.fail_interrupted_work()
            with pytest.raises(FeatureBusyError):
                runtime.initialize()
        finally:
            stage_executor.release.set()
        assert future.result(timeout=5).status == "BLOCKED"
