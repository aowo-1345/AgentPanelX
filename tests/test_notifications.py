"""Unit coverage for the optional, one-way Runtime notifications."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast
from urllib.request import Request

import pytest

from agentplanex.domains.execution_event import ExecutionEvent, ExecutionEventType
from agentplanex.infrastructure.feishu import FeishuWebhookClient
from agentplanex.services.event_bus import EventBus
from agentplanex.services.notifications import NotificationService
from agentplanex.settings import DEFAULT_SETTINGS_PATH, NotificationSettings, load_settings
from tests.runtime_support import compose_test_executions


@dataclass
class _FakeSender:
    messages: list[str] = field(default_factory=list)
    fail: bool = False

    def send(self, text: str) -> None:
        if self.fail:
            raise OSError("network unavailable")
        self.messages.append(text)


def _event(event_type: ExecutionEventType, **payload: object) -> ExecutionEvent:
    return ExecutionEvent(triage_id="feature-feishu", event_type=event_type, payload=payload)


@pytest.mark.parametrize(
    ("event_type", "payload", "expected"),
    [
        (
            ExecutionEventType.PLAN_APPROVAL_REQUESTED,
            {"subject_digest": "plan-123"},
            ("Plan approval required", "WAITING_FOR_HUMAN", "Approve or reject the Plan"),
        ),
        (
            ExecutionEventType.FIRST_RUN_APPROVAL_REQUESTED,
            {"snapshot_id": "snapshot-1", "milestone_key": "milestone-a"},
            ("First Run approval required", "snapshot-1", "milestone-a"),
        ),
        (
            ExecutionEventType.BLOCKED_RUN_APPROVAL_REQUESTED,
            {
                "snapshot_id": "snapshot-2",
                "milestone_key": "milestone-b",
                "failed_run_id": "run-2",
                "failed_stage_key": "stage-b",
            },
            ("Blocked Run approval required", "run-2", "stage-b"),
        ),
        (
            ExecutionEventType.OWNER_ACTIVATION_FAILED,
            {"activation_id": "activation-1", "task_type": "planning", "failure": "gateway"},
            ("Owner activation failed", "activation-1", "planning"),
        ),
        (
            ExecutionEventType.STAGE_RUN_FAILED,
            {
                "stage_run_id": "stage-run-1",
                "run_id": "run-1",
                "milestone_key": "milestone-a",
                "stage_key": "stage-a",
            },
            ("Stage execution failed", "run-1", "stage-a"),
        ),
        (
            ExecutionEventType.AUTO_TAKEOVER_FAILED,
            {"run_id": "run-3", "error": "takeover failed"},
            ("AutoTakeover failed", "run-3", "takeover failed"),
        ),
        (
            ExecutionEventType.TRIAGE_DEVELOPMENT_COMPLETED,
            {"snapshot_id": "snapshot-3", "candidate_commit_sha": "abcdef1234567890"},
            ("Feature development completed", "abcdef1234567890", "COMPLETED"),
        ),
    ],
)
def test_selected_events_render_standard_messages(
    event_type: ExecutionEventType,
    payload: dict[str, object],
    expected: tuple[str, str, str],
) -> None:
    message = NotificationService.render(_event(event_type, **payload))

    assert message is not None
    assert "[AgentPlaneX]" in message
    assert "Feature: feature-feishu" in message
    for value in expected:
        assert value in message


@pytest.mark.parametrize(
    "event_type",
    [
        ExecutionEventType.REACT_LOOP_ENTERED,
        ExecutionEventType.AGENT_INVOCATION_FAILED,
        ExecutionEventType.CONTEXT_COMPACTION_FAILED,
        ExecutionEventType.STAGE_RUN_SUCCEEDED,
        ExecutionEventType.CANDIDATE_READY,
    ],
)
def test_internal_and_non_terminal_events_are_ignored(event_type: ExecutionEventType) -> None:
    sender = _FakeSender()

    NotificationService(sender)(_event(event_type, failure="should not be sent"))

    assert sender.messages == []


def test_failure_summary_is_bounded_and_redacted() -> None:
    message = NotificationService.render(
        _event(
            ExecutionEventType.OWNER_ACTIVATION_FAILED,
            activation_id="activation-1",
            task_type="planning",
            failure=(
                "token=super-secret password=hunter2 "
                "https://internal.example/hook /srv/private/source.py "
                + "x" * 500
            ),
        )
    )

    assert message is not None
    assert "super-secret" not in message
    assert "hunter2" not in message
    assert "internal.example" not in message
    assert "/srv/private" not in message
    assert len(message.split("Reason: ", maxsplit=1)[1]) <= 180


def test_sender_failure_is_isolated_from_other_event_handlers() -> None:
    sender = _FakeSender(fail=True)
    delivered: list[ExecutionEvent] = []

    EventBus(
        handlers=(
            NotificationService(sender),
            delivered.append,
        )
    ).publish(_event(ExecutionEventType.TRIAGE_DEVELOPMENT_COMPLETED))

    assert delivered and delivered[0].event_type is ExecutionEventType.TRIAGE_DEVELOPMENT_COMPLETED


def test_feishu_client_posts_text_json_with_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class _Response:
        status = 200

        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *args: object) -> None:
            return None

    def fake_urlopen(request: object, *, timeout: float) -> _Response:
        captured["request"] = request
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setattr("agentplanex.infrastructure.feishu.urlopen", fake_urlopen)

    FeishuWebhookClient("https://open.feishu.cn/hook/test", timeout_seconds=3.5).send("hello")

    request = cast(Request, captured["request"])
    assert request.method == "POST"
    assert request.full_url == "https://open.feishu.cn/hook/test"
    assert json.loads(request.data.decode()) == {
        "msg_type": "text",
        "content": {"text": "hello"},
    }
    assert captured["timeout"] == 3.5


def test_notification_settings_are_disabled_by_default() -> None:
    assert NotificationSettings().enabled is False


def test_disabled_notifications_are_not_attached_to_runtime(
    initialize_git_project: Callable[[], Path],
) -> None:
    composed = compose_test_executions(initialize_git_project())

    assert len(composed.event_bus.handlers) == 1


def test_missing_webhook_does_not_attach_enabled_notifications(
    initialize_git_project: Callable[[], Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AGENTPLANEX_FEISHU_WEBHOOK", raising=False)
    settings = load_settings(DEFAULT_SETTINGS_PATH)
    runtime_settings = settings.runtime.model_copy(
        update={"notifications": NotificationSettings(enabled=True)}
    )

    composed = compose_test_executions(initialize_git_project(), runtime_settings)

    assert len(composed.event_bus.handlers) == 1
