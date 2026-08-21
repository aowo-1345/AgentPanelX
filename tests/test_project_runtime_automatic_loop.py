"""Single-Feature ProjectRuntime automatic-loop behavior."""

import json
import sqlite3
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event

import pytest

from agentplanex.bootstrap import (
    create_project_control_query,
    create_project_workspace_query,
)
from agentplanex.domains import (
    ExecutionEvent,
    ExecutionEventType,
    OwnerActivation,
    OwnerActivationMode,
    ProjectOwnerTaskType,
    delivery_candidate_ref,
)
from agentplanex.infrastructure.git_repository import GitRepository, GitRepositoryError
from agentplanex.infrastructure.sqlite import SQLiteDatabase
from agentplanex.infrastructure.sqlite.repositories import (
    SQLiteExecutionEventRepository,
    SQLiteMessageHistoryRepository,
    SQLiteOwnerActivationRepository,
    SQLiteProjectOwnerAgentRepository,
    SQLiteStageRunRepository,
)
from agentplanex.project_owner_agent.exception import ModelGatewayError
from agentplanex.project_owner_agent.models.responses import (
    ResponsesRequest,
    ResponsesTransport,
)
from agentplanex.project_runtime.errors import FeatureBusyError
from agentplanex.services.delivery import DeliveryError
from agentplanex.services.delivery._stage_executor import StageExecutionRequest
from agentplanex.services.project_control import ProjectControlQuery
from agentplanex.services.project_workspace import ProjectWorkspaceQuery
from agentplanex.settings import DEFAULT_SETTINGS_PATH, Settings, load_settings
from tests.runtime_support import RuntimePair, compose_test_runtime


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
        update={"project_owner_agent": configured.project_owner_agent.model_copy(update=updates)}
    )


def _write_plan(project_path: Path) -> None:
    for name in ("architecture.md", "requirements.md", "roadmap.md"):
        (project_path / name).write_text(f"# {name}\n", encoding="utf-8")


def _request_first_run(
    runtime: RuntimePair,
    project_path: Path,
    *,
    milestones: list[dict[str, object]] | None = None,
) -> None:
    _write_plan(project_path)
    requested = runtime.control.execute_tool(
        {
            "tool": "request_plan_approval",
            "call_id": "request-plan",
            "arguments": {},
        }
    )
    assert requested.exit is not None
    assert requested.exit.status.value == "PlanApprovalRequested"
    runtime.runtime.approve_plan()
    runtime.runtime.drive_until_waiting()
    published = runtime.control.execute_tool(
        {
            "tool": "update_milestones",
            "call_id": "publish-milestones",
            "arguments": {
                "reason": "Exercise the complete automatic Stage loop.",
                "milestones": milestones
                or [
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
    queued = runtime.control.execute_tool(
        {
            "tool": "run_next_milestone",
            "call_id": "queue-first-run",
            "arguments": {},
        }
    )
    assert queued.exit is not None
    assert queued.exit.status.value == "FirstRunApprovalRequested"


def _queue_first_run(runtime: RuntimePair, project_path: Path) -> None:
    _request_first_run(runtime, project_path)
    runtime.runtime.start_first_run()


def _candidate_decision_arguments(
    runtime: RuntimePair,
    *,
    decision: str,
    reason: str,
) -> dict[str, str]:
    state = runtime.runtime.state()
    assert state.current_snapshot_id is not None
    assert state.current_run_id is not None
    assert state.current_milestone_key is not None
    assert state.current_candidate_commit_sha is not None
    return {
        "snapshot_id": state.current_snapshot_id,
        "run_id": state.current_run_id,
        "milestone_key": state.current_milestone_key,
        "candidate_commit_sha": state.current_candidate_commit_sha,
        "decision": decision,
        "reason": reason,
    }


def test_drive_until_waiting_consumes_message_and_returns_owner_reply(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    runtime = compose_test_runtime(
        project_path=project_path,
        settings=_settings(),
        approval_mode="yolo",
        responses_transport=_ReplyingOwner(),
    )
    runtime.runtime.initialize()
    runtime.runtime.begin_feature()
    activation = runtime.runtime.submit_message("Explain the next implementation step.")

    context = runtime.runtime.drive_until_waiting()

    assert context.triage_id == activation.triage_id
    assert context.status == "TODO"
    workspace = create_project_workspace_query(project_path=project_path).get(activation.triage_id)
    assert workspace.owner_activation is None
    assert [(message.role, message.content) for message in workspace.conversation] == [
        ("user", "Explain the next implementation step."),
        ("assistant", "Owner reached a human waiting point."),
    ]


def test_drive_until_waiting_blocks_runtime_when_owner_fails(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    runtime = compose_test_runtime(
        project_path=project_path,
        settings=_settings(),
        approval_mode="yolo",
        responses_transport=_FailingOwner(),
    )
    runtime.runtime.initialize()
    runtime.runtime.begin_feature()
    runtime.runtime.submit_message("Trigger a deterministic Owner failure.")

    context = runtime.runtime.drive_until_waiting()

    assert context.status == "BLOCKED"
    workspace = create_project_workspace_query(project_path=project_path).get(context.triage_id)
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
    runtime = compose_test_runtime(
        project_path=project_path,
        settings=settings,
        approval_mode="yolo",
        responses_transport=transport,
    )
    runtime.runtime.initialize()
    runtime.runtime.begin_feature()
    runtime.runtime.submit_message("Reach a structured Owner failure.")

    context = runtime.runtime.drive_until_waiting()

    assert context.status == "BLOCKED"
    failure_messages = [
        message.content
        for message in create_project_workspace_query(project_path=project_path)
        .get(context.triage_id)
        .conversation
        if message.role == "status"
    ]
    assert len(failure_messages) == 1
    assert expected_failure in failure_messages[0]


def test_drive_until_waiting_stops_at_plan_approval(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    runtime = compose_test_runtime(
        project_path=project_path,
        settings=_settings(),
        approval_mode="yolo",
        responses_transport=_UnexpectedOwner(),
    )
    runtime.runtime.initialize()
    runtime.runtime.begin_feature()
    _write_plan(project_path)
    runtime.control.execute_tool(
        {
            "tool": "request_plan_approval",
            "call_id": "wait-for-plan-approval",
            "arguments": {},
        }
    )

    context = runtime.runtime.drive_until_waiting()

    assert context.status == "TODO"
    assert context.pending_action == "PLAN_APPROVAL"


def test_message_does_not_clear_runtime_execution_block(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    runtime = compose_test_runtime(
        project_path=project_path,
        settings=_settings(),
        approval_mode="yolo",
        responses_transport=_FailingOwner(),
    )
    runtime.runtime.initialize()
    runtime.runtime.begin_feature()
    runtime.runtime.submit_message("Fail the first activation.")
    assert runtime.runtime.drive_until_waiting().status == "BLOCKED"

    pending = runtime.runtime.submit_message("Record follow-up without resuming work.")
    context = runtime.runtime.drive_until_waiting()

    assert context.status == "BLOCKED"
    workspace = create_project_workspace_query(project_path=project_path).get(context.triage_id)
    assert workspace.owner_activation == pending
    assert workspace.owner_activation.status.value == "PENDING"


def test_drive_until_waiting_leaves_tool_owned_activation_for_human_control(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    runtime = compose_test_runtime(
        project_path=project_path,
        settings=_settings(),
        approval_mode="yolo",
        responses_transport=_UnexpectedOwner(),
    )
    runtime.runtime.initialize()
    runtime.runtime.begin_feature()
    runtime.runtime.submit_message("Run one manually supplied step.")
    step = runtime.control.drive_owner_tool(
        {
            "tool": "bash",
            "call_id": "manual-step",
            "arguments": {"command": "printf controlled"},
        }
    )
    assert step.activation.status.value == "PENDING"
    assert step.activation.driver_mode is not None
    assert step.activation.driver_mode.value == "TOOL"

    context = runtime.runtime.drive_until_waiting()

    assert context.status == "TODO"
    workspace = create_project_workspace_query(project_path=project_path).get(context.triage_id)
    assert workspace.owner_activation == step.activation

    assert runtime.runtime.fail_interrupted_work() is True
    assert runtime.runtime.initialize().status == "BLOCKED"
    assert (
        create_project_workspace_query(project_path=project_path)
        .get(context.triage_id)
        .owner_activation
        is None
    )


def test_manual_activation_failure_blocks_runtime(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    runtime = compose_test_runtime(
        project_path=project_path,
        settings=_settings(),
        approval_mode="yolo",
        responses_transport=_UnexpectedOwner(),
    )
    runtime.runtime.initialize()
    runtime.runtime.begin_feature()
    runtime.runtime.submit_message("Fail this manual activation.")

    failed = runtime.control.fail_owner("Developer stopped the manual drive.")

    assert failed.activation.status.value == "FAILED"
    assert runtime.runtime.initialize().status == "BLOCKED"


def test_drive_until_waiting_runs_all_stages_then_delivers_candidate_to_owner(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    executor = _SuccessfulStageExecutor()
    runtime = compose_test_runtime(
        project_path=project_path,
        settings=_settings(),
        approval_mode="yolo",
        responses_transport=_ReplyingOwner(),
        stage_executor=executor,
    )
    runtime.runtime.initialize()
    runtime.runtime.begin_feature()
    _queue_first_run(runtime, project_path)

    context = runtime.runtime.drive_until_waiting()

    assert executor.executed_stage_keys == ["stage-1", "stage-2"]
    assert context.status == "IN_PROGRESS"
    assert context.current_candidate_commit_sha is not None
    control = create_project_control_query(project_path=project_path).get_current()
    assert [stage.status.value for stage in control.stage_runs] == [
        "SUCCEEDED",
        "SUCCEEDED",
    ]
    assert control.owner_activation is None
    assert create_project_workspace_query(project_path=project_path).get(
        context.triage_id
    ).conversation[-1].content == ("Owner reached a human waiting point.")


def test_first_run_rejects_git_identity_changed_after_plan_approval(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    runtime = compose_test_runtime(
        project_path=project_path,
        settings=_settings(),
        approval_mode="yolo",
        responses_transport=_ReplyingOwner(),
        stage_executor=_SuccessfulStageExecutor(),
    )
    runtime.runtime.initialize()
    runtime.runtime.begin_feature()
    _request_first_run(runtime, project_path)
    approved_plan_commit = runtime.runtime.state().current_plan_commit_sha
    (project_path / "architecture.md").write_text(
        "# architecture.md\n\nChanged after approval.\n",
        encoding="utf-8",
    )
    changed_commit = GitRepository(project_path).commit_paths(
        (project_path / "architecture.md",), message="test: change approved plan identity"
    )
    assert changed_commit != approved_plan_commit

    with pytest.raises(DeliveryError, match=r"Plan Specs changed|Plan approval"):
        runtime.runtime.start_first_run()

    control = create_project_control_query(project_path=project_path).get_current()
    assert control.state.status == "READY"
    assert control.state.pending_action == "FIRST_RUN_APPROVAL"
    assert control.stage_runs == ()


def test_feature_runs_from_user_message_through_owner_tools_to_done(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    runtime = compose_test_runtime(
        project_path=project_path,
        settings=_settings(),
        approval_mode="yolo",
        responses_transport=_UnexpectedOwner(),
        stage_executor=_SuccessfulStageExecutor(),
    )
    initial = runtime.runtime.initialize()
    runtime.runtime.begin_feature()
    _write_plan(project_path)

    user_activation = runtime.runtime.submit_message("Deliver the complete Feature.")
    requested = runtime.control.drive_owner_tool(
        {
            "tool": "request_plan_approval",
            "call_id": "owner-request-plan",
            "arguments": {},
        }
    )
    assert requested.activation.activation_id == user_activation.activation_id
    assert requested.activation.status.value == "COMPLETED"
    plan_decision = runtime.runtime.approve_plan()

    published = runtime.control.drive_owner_tool(
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
                        "stages": [{"key": "stage-1", "objective": "Implement the Feature."}],
                    }
                ],
            },
        }
    )
    assert published.activation.activation_id == plan_decision.activation.activation_id
    queued = runtime.control.drive_owner_tool(
        {
            "tool": "run_next_milestone",
            "call_id": "owner-request-first-run",
            "arguments": {},
        }
    )
    assert queued.activation.status.value == "COMPLETED"
    started = runtime.runtime.start_first_run()
    candidate = runtime.control.drive_delivery()
    assert candidate == "candidate_ready"
    candidate_activation = (
        create_project_control_query(project_path=project_path).get_current().owner_activation
    )
    assert candidate_activation is not None

    accepted = runtime.control.drive_owner_tool(
        {
            "tool": "decide_milestone_candidate",
            "call_id": "owner-accept-candidate",
            "arguments": _candidate_decision_arguments(
                runtime,
                decision="accept",
                reason="Git and Stage evidence match the approved Milestone.",
            ),
        }
    )

    state = runtime.runtime.state()
    assert accepted.activation.activation_id == candidate_activation.activation_id
    assert accepted.activation.status.value == "COMPLETED"
    assert state.status == "DONE"
    assert state.current_candidate_commit_sha is None
    control = create_project_control_query(project_path=project_path).get_current()
    assert [stage.status.value for stage in control.stage_runs] == ["SUCCEEDED"]
    output_commit = control.stage_runs[0].output_commit_sha
    assert output_commit is not None
    git = GitRepository(project_path)
    assert git.head_sha() == output_commit
    assert git.resolve_ref(delivery_candidate_ref(started.run_id)) == output_commit
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
    persisted_messages = tuple(message for history in messages for message in history.message)
    assert any(
        message.get("role") == "user" and message.get("content") == "Deliver the complete Feature."
        for message in persisted_messages
    )
    assert any(message.get("type") == "function_call" for message in persisted_messages)
    event_types = [event.event_type.value for event in control.timeline]
    assert event_types.index("CANDIDATE_READY") < event_types.index("TRIAGE_DEVELOPMENT_COMPLETED")
    assert "CANDIDATE_ACCEPTED" in event_types


def test_accept_rolls_forward_to_next_milestone_then_done(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    runtime = compose_test_runtime(
        project_path=project_path,
        settings=_settings(),
        approval_mode="yolo",
        responses_transport=_ReplyingOwner(),
        stage_executor=_SuccessfulStageExecutor(),
    )
    runtime.runtime.initialize()
    runtime.runtime.begin_feature()
    _request_first_run(
        runtime,
        project_path,
        milestones=[
            {
                "key": "m1",
                "objective": "First.",
                "state": "pending",
                "stages": [{"key": "s1", "objective": "First."}],
            },
            {
                "key": "m2",
                "objective": "Second.",
                "state": "pending",
                "stages": [{"key": "s2", "objective": "Second."}],
            },
        ],
    )
    runtime.runtime.start_first_run()
    runtime.runtime.drive_until_waiting()
    first = runtime.control.execute_tool(
        {
            "tool": "decide_milestone_candidate",
            "arguments": _candidate_decision_arguments(
                runtime, decision="accept", reason="Accept first."
            ),
        }
    )
    assert first.output["completed"] is False
    view = create_project_control_query(project_path=project_path).get_current()
    assert view.snapshot is not None
    assert [item.state.value for item in view.snapshot.milestones] == ["completed", "pending"]
    queued = runtime.control.execute_tool({"tool": "run_next_milestone", "arguments": {}})
    assert queued.output["milestone_key"] == "m2"
    runtime.runtime.drive_until_waiting()
    final = runtime.control.execute_tool(
        {
            "tool": "decide_milestone_candidate",
            "arguments": _candidate_decision_arguments(
                runtime, decision="accept", reason="Accept final."
            ),
        }
    )
    assert final.output["completed"] is True
    assert runtime.runtime.state().status == "DONE"


def test_accept_sqlite_failure_rolls_forward_same_candidate_from_blocked(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    runtime = compose_test_runtime(
        project_path=project_path,
        settings=_settings(),
        approval_mode="yolo",
        responses_transport=_ReplyingOwner(),
        stage_executor=_SuccessfulStageExecutor(),
    )
    runtime.runtime.initialize()
    runtime.runtime.begin_feature()
    _queue_first_run(runtime, project_path)
    assert runtime.control.drive_delivery() == "stage_succeeded"
    assert runtime.control.drive_delivery() == "candidate_ready"
    arguments = _candidate_decision_arguments(runtime, decision="accept", reason="Retry accept.")
    database = SQLiteDatabase.for_project(project_path)
    with database.transaction() as connection:
        connection.execute(
            """CREATE TRIGGER fail_candidate_decision
            BEFORE INSERT ON milestone_snapshot
            BEGIN SELECT RAISE(ABORT, 'forced'); END"""
        )
    failed = runtime.control.drive_owner_tool(
        {
            "tool": "decide_milestone_candidate",
            "call_id": "accept-with-sqlite-fault",
            "arguments": arguments,
        }
    )
    assert failed.activation.status.value == "FAILED"
    assert runtime.runtime.state().status == "BLOCKED"
    assert GitRepository(project_path).head_sha() == arguments["candidate_commit_sha"]
    with database.transaction() as connection:
        connection.execute("DROP TRIGGER fail_candidate_decision")
    rejected = runtime.control.execute_tool(
        {"tool": "decide_milestone_candidate", "arguments": {**arguments, "decision": "reject"}}
    )
    assert rejected.output["ok"] is False
    retried = runtime.control.execute_tool(
        {"tool": "decide_milestone_candidate", "arguments": arguments}
    )
    assert retried.output["ok"] is True
    assert runtime.runtime.state().status == "DONE"


def test_reject_sqlite_failure_keeps_candidate_retryable(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    runtime = compose_test_runtime(
        project_path=project_path,
        settings=_settings(),
        approval_mode="yolo",
        responses_transport=_ReplyingOwner(),
        stage_executor=_SuccessfulStageExecutor(),
    )
    runtime.runtime.initialize()
    runtime.runtime.begin_feature()
    _queue_first_run(runtime, project_path)
    runtime.runtime.drive_until_waiting()
    arguments = _candidate_decision_arguments(runtime, decision="reject", reason="Needs revision.")
    before = runtime.runtime.state()
    stale = runtime.control.execute_tool(
        {
            "tool": "decide_milestone_candidate",
            "arguments": {**arguments, "run_id": "stale-run"},
        }
    )
    assert stale.output["ok"] is False
    assert runtime.runtime.state() == before
    database = SQLiteDatabase.for_project(project_path)
    with database.transaction() as connection:
        connection.execute(
            """CREATE TRIGGER fail_candidate_reject
            BEFORE INSERT ON milestone_snapshot
            BEGIN SELECT RAISE(ABORT, 'forced'); END"""
        )
    with pytest.raises(sqlite3.IntegrityError):
        runtime.control.execute_tool({"tool": "decide_milestone_candidate", "arguments": arguments})
    assert runtime.runtime.state() == before
    with database.transaction() as connection:
        connection.execute("DROP TRIGGER fail_candidate_reject")
    retried = runtime.control.execute_tool(
        {"tool": "decide_milestone_candidate", "arguments": arguments}
    )
    assert retried.output["ok"] is True
    view = create_project_control_query(project_path=project_path).get_current()
    assert view.snapshot is not None
    assert view.snapshot.reason == "Needs revision."


def test_drive_until_waiting_returns_immediately_when_runtime_is_done(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    executor = _SuccessfulStageExecutor()
    runtime = compose_test_runtime(
        project_path=project_path,
        settings=_settings(),
        approval_mode="yolo",
        responses_transport=_ReplyingOwner(),
        stage_executor=executor,
    )
    runtime.runtime.initialize()
    runtime.runtime.begin_feature()
    _queue_first_run(runtime, project_path)
    runtime.runtime.drive_until_waiting()
    decision = runtime.control.execute_tool(
        {
            "tool": "decide_milestone_candidate",
            "call_id": "accept-only-candidate",
            "arguments": _candidate_decision_arguments(
                runtime,
                decision="accept",
                reason="The deterministic Candidate satisfies the Milestone.",
            ),
        }
    )
    assert decision.exit is not None
    assert decision.exit.status.value == "TriageDevelopmentCompleted"
    executed_before_wait = list(executor.executed_stage_keys)

    context = runtime.runtime.drive_until_waiting()

    assert context.status == "DONE"
    assert executor.executed_stage_keys == executed_before_wait


def test_interrupted_owner_work_cannot_demote_completed_feature(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    runtime = compose_test_runtime(
        project_path=project_path,
        settings=_settings(),
        approval_mode="yolo",
        responses_transport=_ReplyingOwner(),
        stage_executor=_SuccessfulStageExecutor(),
    )
    runtime.runtime.initialize()
    runtime.runtime.begin_feature()
    _queue_first_run(runtime, project_path)
    runtime.runtime.drive_until_waiting()
    runtime.control.execute_tool(
        {
            "tool": "decide_milestone_candidate",
            "call_id": "complete-before-interruption",
            "arguments": _candidate_decision_arguments(
                runtime,
                decision="accept",
                reason="Complete the Feature before recovery runs.",
            ),
        }
    )
    state = runtime.runtime.state()
    assert state.status == "DONE"
    database = SQLiteDatabase.for_project(project_path)
    with database.transaction() as connection:
        SQLiteOwnerActivationRepository().insert(
            connection,
            OwnerActivation(
                activation_id="late-interrupted-activation",
                triage_id=state.triage_id,
                task_type=ProjectOwnerTaskType.EXECUTION_RESULT,
                message_id="late-result-message",
            ),
        )

    assert runtime.runtime.fail_interrupted_work() is True
    assert runtime.runtime.state().status == "DONE"


def test_stage_failure_blocks_without_feedback_and_retries_only_by_owner_action(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    runtime = compose_test_runtime(
        project_path=project_path,
        settings=_settings(),
        approval_mode="yolo",
        responses_transport=_ReplyingOwner(),
        stage_executor=_FailingStageExecutor(),
    )
    runtime.runtime.initialize()
    runtime.runtime.begin_feature()
    _queue_first_run(runtime, project_path)

    context = runtime.runtime.drive_until_waiting()

    assert context.status == "BLOCKED"
    control = create_project_control_query(project_path=project_path).get_current()
    assert [stage.status.value for stage in control.stage_runs] == ["FAILED"]
    assert control.owner_activation is None
    conversation = (
        create_project_workspace_query(project_path=project_path)
        .get(context.triage_id)
        .conversation
    )
    assert all("Stage execution failed" not in item.content for item in conversation)

    database = SQLiteDatabase.for_project(project_path)
    with database.read_only_connection() as connection:
        owner = SQLiteProjectOwnerAgentRepository().get_by_triage_id(
            connection,
            context.triage_id,
        )
    assert owner is not None
    owner_session_id = owner.project_owner_session_id
    follow_up = runtime.runtime.submit_message("Retry the failed Milestone deliberately.")
    waiting = runtime.runtime.drive_until_waiting()
    assert waiting.status == "BLOCKED"
    assert (
        create_project_control_query(project_path=project_path).get_current().owner_activation
        == follow_up
    )

    retried = runtime.control.drive_owner_tool(
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
    resumed = runtime.runtime.initialize()
    assert resumed.status == "IN_PROGRESS"
    with database.read_only_connection() as connection:
        restored_owner = SQLiteProjectOwnerAgentRepository().get_by_triage_id(
            connection,
            resumed.triage_id,
        )
    assert restored_owner is not None
    assert restored_owner.project_owner_session_id == owner_session_id
    assert [
        stage.status.value
        for stage in create_project_control_query(project_path=project_path)
        .get_current()
        .stage_runs
    ] == [
        "FAILED",
        "QUEUED",
    ]


def test_final_stage_handoff_failure_rolls_back_candidate_and_timeline(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    runtime = compose_test_runtime(
        project_path=project_path,
        settings=_settings(),
        approval_mode="yolo",
        responses_transport=_ReplyingOwner(),
        stage_executor=_SuccessfulStageExecutor(),
    )
    runtime.runtime.initialize()
    runtime.runtime.begin_feature()
    _queue_first_run(runtime, project_path)
    assert runtime.control.drive_delivery() == "stage_succeeded"

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

    result = runtime.control.drive_delivery()

    assert result == "stage_failed"
    state = runtime.runtime.state()
    assert state.status == "BLOCKED"
    assert state.current_candidate_commit_sha is None
    control = create_project_control_query(project_path=project_path).get_current()
    assert control.owner_activation is None
    assert [stage.status.value for stage in control.stage_runs] == [
        "SUCCEEDED",
        "FAILED",
    ]
    assert "CANDIDATE_READY" not in {event.event_type.value for event in control.timeline}
    run_id = control.stage_runs[-1].run_id
    with pytest.raises(GitRepositoryError):
        GitRepository(project_path).resolve_ref(delivery_candidate_ref(run_id))
    with database.read_only_connection() as connection:
        activation_count = connection.execute("SELECT COUNT(*) FROM owner_activation").fetchone()[0]
    assert activation_count == activation_count_before


def test_candidate_cleanup_failure_preserves_failed_state_and_orphan_ref(
    initialize_git_project: Callable[[], Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_path = initialize_git_project()
    runtime = compose_test_runtime(
        project_path=project_path,
        settings=_settings(),
        approval_mode="yolo",
        responses_transport=_ReplyingOwner(),
        stage_executor=_SuccessfulStageExecutor(),
    )
    runtime.runtime.initialize()
    runtime.runtime.begin_feature()
    _queue_first_run(runtime, project_path)
    assert runtime.control.drive_delivery() == "stage_succeeded"
    database = SQLiteDatabase.for_project(project_path)
    with database.transaction() as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_candidate_activation_cleanup_failure
            BEFORE INSERT ON owner_activation
            BEGIN
                SELECT RAISE(ABORT, 'forced candidate handoff rollback');
            END
            """
        )

    def reject_cleanup(
        _repository: GitRepository,
        _ref_name: str,
        *,
        expected_sha: str,
    ) -> None:
        assert expected_sha
        raise GitRepositoryError("forced cleanup failure")

    monkeypatch.setattr(GitRepository, "delete_ref", reject_cleanup)
    assert runtime.control.drive_delivery() == "stage_failed"

    control = create_project_control_query(project_path=project_path).get_current()
    assert control.state.status == "BLOCKED"
    assert control.state.current_candidate_commit_sha is None
    assert control.stage_runs[-1].status.value == "FAILED"
    run_id = control.stage_runs[-1].run_id
    orphan_commit = GitRepository(project_path).resolve_ref(delivery_candidate_ref(run_id))
    assert orphan_commit != control.state.current_candidate_commit_sha


def test_drive_until_waiting_rejects_two_simultaneously_runnable_work_items(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    runtime = compose_test_runtime(
        project_path=project_path,
        settings=_settings(),
        approval_mode="yolo",
        responses_transport=_ReplyingOwner(),
        stage_executor=_SuccessfulStageExecutor(),
    )
    initialized = runtime.runtime.initialize()
    runtime.runtime.begin_feature()
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
        runtime.runtime.drive_until_waiting()


def test_fail_interrupted_work_terminalizes_pending_activation_without_model(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    runtime = compose_test_runtime(
        project_path=project_path,
        settings=_settings(),
        approval_mode="yolo",
        responses_transport=_UnexpectedOwner(),
    )
    runtime.runtime.initialize()
    runtime.runtime.begin_feature()
    pending = runtime.runtime.submit_message("This accepted work was interrupted before claim.")

    assert runtime.runtime.fail_interrupted_work() is True
    assert runtime.runtime.fail_interrupted_work() is False

    context = runtime.runtime.initialize()
    assert context.status == "BLOCKED"
    workspace = create_project_workspace_query(project_path=project_path).get(context.triage_id)
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
        for event in create_project_control_query(project_path=project_path).get_current().timeline
        if event.event_type.value == "OWNER_ACTIVATION_FAILED"
    )
    assert event.payload["activation_id"] == pending.activation_id
    assert event.payload["started"] is False
    assert event.payload["interrupted"] is True


def test_interrupted_running_owner_closes_active_timeline_loop(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    runtime = compose_test_runtime(
        project_path=project_path,
        settings=_settings(),
        approval_mode="yolo",
        responses_transport=_UnexpectedOwner(),
    )
    state = runtime.runtime.initialize()
    runtime.runtime.begin_feature()
    pending = runtime.runtime.submit_message("Simulate a process crash after claim.")
    database = SQLiteDatabase.for_project(project_path)
    events = SQLiteExecutionEventRepository()
    with database.transaction() as connection:
        running = SQLiteOwnerActivationRepository().claim_next(
            connection,
            state.triage_id,
            datetime.now(UTC),
            OwnerActivationMode.MODEL,
        )
        assert running is not None
        events.insert(
            connection,
            ExecutionEvent(
                triage_id=state.triage_id,
                event_type=ExecutionEventType.REACT_LOOP_ENTERED,
                react_loop_id=pending.activation_id,
                payload={"driver_mode": "MODEL"},
            ),
        )

    assert runtime.runtime.fail_interrupted_work() is True

    with database.connection() as connection:
        assert events.get_active_react_loop_id(connection, state.triage_id) is None
        timeline = events.list_by_triage_id(connection, state.triage_id)
    owner_events = [
        event
        for event in timeline
        if event.event_type
        in {
            ExecutionEventType.REACT_LOOP_ENTERED,
            ExecutionEventType.REACT_LOOP_EXITED,
            ExecutionEventType.OWNER_ACTIVATION_FAILED,
        }
    ]
    assert [event.event_type for event in owner_events] == [
        ExecutionEventType.REACT_LOOP_ENTERED,
        ExecutionEventType.REACT_LOOP_EXITED,
        ExecutionEventType.OWNER_ACTIVATION_FAILED,
    ]
    assert owner_events[1].react_loop_id == pending.activation_id
    assert owner_events[1].payload["interrupted"] is True


def test_fail_interrupted_work_terminalizes_queued_stage_and_preserves_cursor(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    runtime = compose_test_runtime(
        project_path=project_path,
        settings=_settings(),
        approval_mode="yolo",
        responses_transport=_ReplyingOwner(),
        stage_executor=_SuccessfulStageExecutor(),
    )
    runtime.runtime.initialize()
    runtime.runtime.begin_feature()
    _queue_first_run(runtime, project_path)
    before = runtime.runtime.initialize()

    assert runtime.runtime.fail_interrupted_work() is True

    after = runtime.runtime.initialize()
    assert after.status == "BLOCKED"
    assert after.current_snapshot_id == before.current_snapshot_id
    assert after.current_run_id == before.current_run_id
    assert after.current_milestone_key == before.current_milestone_key
    assert after.current_stage_key == before.current_stage_key
    control = create_project_control_query(project_path=project_path).get_current()
    assert [stage.status.value for stage in control.stage_runs] == ["FAILED"]
    assert control.stage_runs[0].started_at is None
    assert control.owner_activation is None
    event = next(
        event
        for event in control.timeline
        if event.event_type.value == "STAGE_RUN_FAILED" and event.payload.get("interrupted") is True
    )
    assert event.payload["stage_run_id"] == control.stage_runs[0].stage_run_id
    assert event.payload["started"] is False


def test_expired_stage_lease_fails_without_reexecuting_adapter(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    stage_executor = _SuccessfulStageExecutor()
    runtime = compose_test_runtime(
        project_path=project_path,
        settings=_settings(),
        approval_mode="yolo",
        responses_transport=_ReplyingOwner(),
        stage_executor=stage_executor,
    )
    state = runtime.runtime.initialize()
    runtime.runtime.begin_feature()
    _queue_first_run(runtime, project_path)
    now = datetime.now(UTC)
    database = SQLiteDatabase.for_project(project_path)
    with database.transaction() as connection:
        claimed = SQLiteStageRunRepository().claim_next(
            connection,
            state.triage_id,
            started_at=now - timedelta(hours=2),
            lease_expires_at=now - timedelta(hours=1),
        )
    assert claimed is not None

    assert runtime.control.drive_delivery() == "stage_failed"
    assert stage_executor.executed_stage_keys == []
    control = create_project_control_query(project_path=project_path).get_current()
    assert control.state.status == "BLOCKED"
    assert control.stage_runs[-1].status.value == "FAILED"
    assert "lease expired" in (control.stage_runs[-1].failure or "")


def test_running_activation_rejects_concurrent_interruption_recovery(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    owner = _BlockingOwner()
    runtime = compose_test_runtime(
        project_path=project_path,
        settings=_settings(),
        approval_mode="yolo",
        responses_transport=owner,
    )
    runtime.runtime.initialize()
    runtime.runtime.begin_feature()
    runtime.runtime.submit_message("This activation was claimed before interruption.")
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(runtime.runtime.drive_until_waiting)
        assert owner.entered.wait(timeout=5)
        try:
            with pytest.raises(FeatureBusyError):
                runtime.runtime.fail_interrupted_work()
            with pytest.raises(FeatureBusyError):
                runtime.runtime.initialize()
        finally:
            owner.release.set()
        assert future.result(timeout=5).status == "BLOCKED"


def test_running_stage_rejects_concurrent_interruption_recovery(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    stage_executor = _BlockingStageExecutor()
    runtime = compose_test_runtime(
        project_path=project_path,
        settings=_settings(),
        approval_mode="yolo",
        responses_transport=_ReplyingOwner(),
        stage_executor=stage_executor,
    )
    state = runtime.runtime.initialize()
    runtime.runtime.begin_feature()
    _queue_first_run(runtime, project_path)
    database = SQLiteDatabase.for_project(project_path)
    git = GitRepository(project_path)
    control_query = ProjectControlQuery(database=database, git=git)
    workspace_query = ProjectWorkspaceQuery(database=database, git=git)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(runtime.runtime.drive_until_waiting)
        assert stage_executor.entered.wait(timeout=5)
        try:
            control = control_query.get_current()
            workspace = workspace_query.get(state.triage_id)
            assert control.state.status == "IN_PROGRESS"
            assert control.stage_runs[-1].status.value == "RUNNING"
            assert workspace.state == control.state
            with database.transaction() as connection:
                SQLiteExecutionEventRepository().insert(
                    connection,
                    ExecutionEvent(
                        triage_id=state.triage_id,
                        event_type=ExecutionEventType.AGENT_INVOCATION_STARTED,
                        payload={"operation": "concurrent-writer-proof"},
                    ),
                )
            with pytest.raises(FeatureBusyError):
                runtime.runtime.fail_interrupted_work()
            with pytest.raises(FeatureBusyError):
                runtime.runtime.initialize()
        finally:
            stage_executor.release.set()
        assert future.result(timeout=5).status == "BLOCKED"
