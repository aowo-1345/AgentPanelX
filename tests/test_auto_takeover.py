"""Core Ultra Mode behavior through real Runtime boundaries."""

import json
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from threading import Event
from time import monotonic, sleep

import pytest
import yaml

from agentplanex.bootstrap import create_project_control_query, create_workspace
from agentplanex.domains.execution_event import ExecutionEventType
from agentplanex.domains.workspace import FeatureBinding
from agentplanex.infrastructure.codex import CodexTurnRequest, CodexTurnResult, CodexTurnTransport
from agentplanex.infrastructure.git_repository import GitRepository
from agentplanex.infrastructure.sqlite import SQLiteDatabase, initialize_schema
from agentplanex.infrastructure.sqlite.repositories.auto_takeover import (
    AutoTakeoverFenceError,
    SQLiteAutoTakeoverRepository,
)
from agentplanex.project_owner_agent.models.responses import ResponsesRequest, ResponsesTransport
from agentplanex.project_runtime.composition import compose_external_agent_runtime
from agentplanex.services.auto_takeover import AutoTakeoverService
from agentplanex.services.auto_takeover.models import TakeoverStatus
from agentplanex.services.delivery._stage_executor import StageExecutionRequest
from agentplanex.services.workspace.dispatcher import WorkspaceDispatcher
from agentplanex.settings import DEFAULT_SETTINGS_PATH, load_settings
from tests.runtime_support import compose_test_runtime


class _UnusedOwner(ResponsesTransport):
    def create(self, _request: ResponsesRequest) -> object:
        raise AssertionError("This test does not drive the Owner model")


class _ReplyingOwner(ResponsesTransport):
    def create(self, _request: ResponsesRequest) -> object:
        return {
            "object": "response",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Waiting."}],
                }
            ],
        }


class _FailingStage:
    def execute(self, _request: StageExecutionRequest) -> None:
        raise RuntimeError("controlled Stage failure")


def _runtime_ready_to_fail(project_path: Path):
    settings = load_settings(DEFAULT_SETTINGS_PATH)
    pair = compose_test_runtime(
        project_path=project_path,
        settings=settings,
        approval_mode="yolo",
        responses_transport=_ReplyingOwner(),
        stage_executor=_FailingStage(),
    )
    pair.runtime.initialize()
    pair.runtime.begin_feature()
    for name in ("architecture.md", "requirements.md", "roadmap.md"):
        (project_path / name).write_text(f"# {name}\n", encoding="utf-8")
    pair.control.execute_tool(
        {"tool": "request_plan_approval", "call_id": "plan", "arguments": {}}
    )
    pair.runtime.approve_plan()
    pair.runtime.drive_until_waiting()
    pair.control.execute_tool(
        {
            "tool": "update_milestones",
            "call_id": "milestones",
            "arguments": {
                "reason": "Exercise deterministic takeover.",
                "milestones": [
                    {
                        "key": "milestone-1",
                        "objective": "Recover a failed delivery.",
                        "state": "pending",
                        "stages": [
                            {"key": "stage-1", "objective": "Fail deterministically."}
                        ],
                    }
                ],
            },
        }
    )
    pair.control.execute_tool(
        {"tool": "run_next_milestone", "call_id": "first", "arguments": {}}
    )
    pair.runtime.start_first_run()
    return pair


def _blocked_runtime(project_path: Path):
    pair = _runtime_ready_to_fail(project_path)
    assert pair.runtime.drive_until_waiting().status == "BLOCKED"
    return pair


def _request_path(request: CodexTurnRequest, name: str) -> Path:
    match = re.search(rf'"{name}": "([^"]+)"', request.message)
    assert match is not None
    return Path(json.loads(f'"{match.group(1)}"'))


def _fence(request: CodexTurnRequest) -> str:
    match = re.search(r'--takeover-fence",\s*"([^"]+)"', request.message)
    assert match is not None
    return match.group(1)


def _wait(service: AutoTakeoverService, binding: FeatureBinding, phase: str) -> None:
    deadline = monotonic() + 5
    while monotonic() < deadline:
        snapshot = service.snapshot(binding)
        if snapshot is not None and snapshot.phase == phase:
            return
        sleep(0.01)
    raise AssertionError(f"AutoTakeover did not reach {phase}")


def _service(project_path: Path, scheduled: Event) -> AutoTakeoverService:
    settings = load_settings(DEFAULT_SETTINGS_PATH)
    return AutoTakeoverService(
        external_runtime_factory=lambda path: compose_external_agent_runtime(
            project_path=path,
            settings=settings,
        ),
        schedule_drive=lambda _binding: scheduled.set(),
        settings_path=DEFAULT_SETTINGS_PATH.resolve(),
        budget_seconds=30,
        max_parallel_features=1,
    )


def test_takeover_repository_fences_and_corrects_one_attempt(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    database = SQLiteDatabase.for_project(project_path)
    initialize_schema(database)
    repository = SQLiteAutoTakeoverRepository()

    with database.transaction() as connection:
        started = repository.begin(connection, triage_id="feature-1", trigger_event_id=7)
        assert started is not None
        run, first = started
        repository.require_active_fence(connection, "feature-1", first.fence_token)
        with pytest.raises(AutoTakeoverFenceError):
            repository.require_active_fence(connection, "feature-1", None)
        second = repository.correct(connection, run.run_id, "YES did not match Runtime")
        with pytest.raises(AutoTakeoverFenceError):
            repository.require_active_fence(connection, "feature-1", first.fence_token)
        repository.require_active_fence(connection, "feature-1", second.fence_token)
        repository.complete(connection, run.run_id, TakeoverStatus.FAILED, error="done")
        repository.require_active_fence(connection, "feature-1", None)


def test_project_control_requires_the_current_takeover_fence(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    settings = load_settings(DEFAULT_SETTINGS_PATH)
    runtime = compose_test_runtime(
        project_path=project_path,
        settings=settings,
        approval_mode="yolo",
        responses_transport=_UnusedOwner(),
    )
    state = runtime.runtime.initialize()
    runtime.runtime.begin_feature()
    database = SQLiteDatabase.for_project(project_path)
    repository = SQLiteAutoTakeoverRepository()
    with database.transaction() as connection:
        started = repository.begin(connection, triage_id=state.triage_id, trigger_event_id=9)
    assert started is not None
    run, attempt = started

    with pytest.raises(AutoTakeoverFenceError):
        runtime.control.submit_message("This mutation has no takeover fence.")

    fenced = compose_test_runtime(
        project_path=project_path,
        settings=settings,
        approval_mode="yolo",
        responses_transport=_UnusedOwner(),
        mutation_fence_token=attempt.fence_token,
    )
    fenced.control.submit_message("Continue through the fenced Control path.")
    with database.transaction() as connection:
        repository.correct(connection, run.run_id, "Invalidate the first attempt")
    with pytest.raises(AutoTakeoverFenceError):
        fenced.control.fail_owner("The stale fence must not mutate Runtime")


def test_real_blocked_transition_starts_codex_after_dispatcher_release_and_restores_run(
    initialize_git_project: Callable[[], Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_path = initialize_git_project()
    runtime = _runtime_ready_to_fail(project_path)
    state = runtime.runtime.state()
    binding = FeatureBinding(state.triage_id, "project-1", "Feature", project_path)
    dispatcher = WorkspaceDispatcher(max_parallel_features=1)
    scheduled = Event()
    callback_entered = Event()
    requests: list[CodexTurnRequest] = []

    def run(
        _transport: CodexTurnTransport,
        request: CodexTurnRequest,
        *,
        on_thread_opened: object | None = None,
    ) -> CodexTurnResult:
        requests.append(request)
        dispatcher.exclusive(binding.triage_id, callback_entered.set)
        if callable(on_thread_opened):
            on_thread_opened("takeover-thread")
        fenced = compose_test_runtime(
            project_path=project_path,
            settings=load_settings(DEFAULT_SETTINGS_PATH),
            approval_mode="yolo",
            responses_transport=_ReplyingOwner(),
            stage_executor=_FailingStage(),
            mutation_fence_token=_fence(request),
        )
        fenced.control.submit_message("Retry the failed Stage under existing intent.")
        requested = fenced.control.drive_owner_tool(
            {"tool": "run_next_milestone", "call_id": "retry", "arguments": {}}
        )
        assert requested.exit is not None
        assert requested.exit.status.value == "BlockedRunApprovalRequested"
        fenced.control.approve_blocked_run()
        _request_path(request, "result_path").write_text(
            '{"version":1,"decision":"YES"}',
            encoding="utf-8",
        )
        return CodexTurnResult(
            "takeover-thread",
            "turn-1",
            "completed",
            '{"summary":"restored"}',
        )

    monkeypatch.setattr(CodexTurnTransport, "run", run)
    service = _service(project_path, scheduled)
    try:
        watermark = service.event_watermark(binding)
        dispatcher.dispatch(
            binding.triage_id,
            persist=lambda: None,
            drive=runtime.runtime.drive_until_waiting,
            after_release=lambda: service.after_drive_released(
                binding,
                after_event_id=watermark,
            ),
        )
        assert callback_entered.wait(timeout=5)
        assert scheduled.wait(timeout=5)
        _wait(service, binding, "recovered")
        service.after_drive_released(binding, after_event_id=watermark)
        sleep(0.05)
    finally:
        service.close()
        dispatcher.close()

    assert len(requests) == 1
    assert {name for name, _path in requests[0].skills} == {
        "agentplanex-project-observe",
        "agentplanex-project-control",
        "agentplanex-project-attribution",
    }
    assert runtime.runtime.state().status == "IN_PROGRESS"
    timeline = create_project_control_query(project_path=project_path).get_current().timeline
    assert any(event.event_type is ExecutionEventType.AUTO_TAKEOVER_COMPLETED for event in timeline)


def test_false_yes_gets_one_same_session_correction_then_no_with_attribution(
    initialize_git_project: Callable[[], Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_path = initialize_git_project()
    runtime = _blocked_runtime(project_path)
    state = runtime.runtime.state()
    binding = FeatureBinding(state.triage_id, "project-1", "Feature", project_path)
    requests: list[CodexTurnRequest] = []
    scheduled = Event()

    def run(
        _transport: CodexTurnTransport,
        request: CodexTurnRequest,
        *,
        on_thread_opened: object | None = None,
    ) -> CodexTurnResult:
        requests.append(request)
        if callable(on_thread_opened) and request.thread_id is None:
            on_thread_opened("takeover-thread")
        if len(requests) == 1:
            _request_path(request, "result_path").write_text(
                '{"version":1,"decision":"YES"}',
                encoding="utf-8",
            )
        else:
            _request_path(request, "attribution_document_path").write_text(
                "# BLOCKED 归因与优化 Proposal\n\n需要真实用户提供新意图。\n",
                encoding="utf-8",
            )
            _request_path(request, "result_path").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "decision": "NO",
                        "attribution": {
                            "path": "documents/attribution.md",
                            "media_type": "text/markdown",
                        },
                    }
                ),
                encoding="utf-8",
            )
        return CodexTurnResult(
            "takeover-thread",
            f"turn-{len(requests)}",
            "completed",
            '{"summary":"done"}',
        )

    monkeypatch.setattr(CodexTurnTransport, "run", run)
    service = _service(project_path, scheduled)
    try:
        service.after_drive_released(binding, after_event_id=0)
        _wait(service, binding, "blocked")
        snapshot = service.snapshot(binding)
    finally:
        service.close()

    assert not scheduled.is_set()
    assert len(requests) == 2
    assert requests[0].thread_id is None
    assert requests[1].thread_id == "takeover-thread"
    assert "runtime_correction" in requests[1].message
    assert snapshot is not None and snapshot.attribution is not None
    external_runtime = compose_external_agent_runtime(
        project_path=project_path,
        settings=load_settings(DEFAULT_SETTINGS_PATH),
    )
    proposal = external_runtime.workspaces.read_descriptor_text(snapshot.attribution)
    source_commit = GitRepository(DEFAULT_SETTINGS_PATH.resolve().parent.parent).head_sha()
    assert state.git_main_version is not None
    assert proposal.startswith(
        "## Source Commits\n\n"
        f"- AgentPanelX Source Commit: `{source_commit}`\n"
        f"- Target Feature Commit: `{state.git_main_version}`\n\n"
        "# BLOCKED 归因与优化 Proposal\n"
    )
    assert runtime.runtime.state().status == "BLOCKED"


def test_second_inconsistent_result_terminalizes_failed_without_fake_attribution(
    initialize_git_project: Callable[[], Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_path = initialize_git_project()
    runtime = _blocked_runtime(project_path)
    state = runtime.runtime.state()
    binding = FeatureBinding(state.triage_id, "project-1", "Feature", project_path)
    calls = 0

    def run(
        _transport: CodexTurnTransport,
        request: CodexTurnRequest,
        *,
        on_thread_opened: object | None = None,
    ) -> CodexTurnResult:
        nonlocal calls
        calls += 1
        if callable(on_thread_opened) and request.thread_id is None:
            on_thread_opened("takeover-thread")
        _request_path(request, "result_path").write_text(
            '{"version":1,"decision":"YES"}',
            encoding="utf-8",
        )
        return CodexTurnResult(
            "takeover-thread",
            f"turn-{calls}",
            "completed",
            '{"summary":"wrong"}',
        )

    monkeypatch.setattr(CodexTurnTransport, "run", run)
    service = _service(project_path, Event())
    try:
        service.after_drive_released(binding, after_event_id=0)
        _wait(service, binding, "failed")
        snapshot = service.snapshot(binding)
    finally:
        service.close()

    assert calls == 2
    assert snapshot is not None and snapshot.attribution is None
    assert snapshot.error is not None and "after correction" in snapshot.error


def test_ultra_mode_uses_explicit_settings_path_without_publishing_a_snapshot(
    tmp_path: Path,
) -> None:
    base = load_settings(DEFAULT_SETTINGS_PATH)
    configured = base.model_copy(
        update={"workspace": base.workspace.model_copy(update={"data_home": tmp_path / "data"})}
    )
    config_path = tmp_path / "settings.yaml"
    config_path.write_text(
        yaml.safe_dump(configured.model_dump(mode="json"), sort_keys=True),
        encoding="utf-8",
    )
    workspace = create_workspace(configured, settings_path=config_path)
    try:
        assert workspace.auto_takeover is not None
        assert workspace.auto_takeover.settings_path == config_path.resolve()
        assert not (configured.workspace.data_home / "config-snapshots").exists()
    finally:
        workspace.close()


def test_packaged_control_command_uses_the_real_fenced_runtime(
    initialize_git_project: Callable[[], Path],
    tmp_path: Path,
) -> None:
    project_path = initialize_git_project()
    settings = load_settings(DEFAULT_SETTINGS_PATH)
    config_path = tmp_path / "settings.yaml"
    config_path.write_text(
        yaml.safe_dump(settings.model_dump(mode="json"), sort_keys=True),
        encoding="utf-8",
    )
    runtime = compose_test_runtime(
        project_path=project_path,
        settings=settings,
        approval_mode="yolo",
        responses_transport=_UnusedOwner(),
    )
    state = runtime.runtime.initialize()
    runtime.runtime.begin_feature()
    database = SQLiteDatabase.for_project(project_path)
    with database.transaction() as connection:
        started = SQLiteAutoTakeoverRepository().begin(
            connection,
            triage_id=state.triage_id,
            trigger_event_id=41,
        )
    assert started is not None
    _run, attempt = started
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "agentplanex.app_cli",
            "--config",
            str(config_path),
            "auto-control",
            "--cwd",
            str(project_path),
            "--takeover-fence",
            str(attempt.fence_token),
            "--print",
            "message continue through the real Runtime",
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert json.loads(completed.stdout)["ok"] is True
