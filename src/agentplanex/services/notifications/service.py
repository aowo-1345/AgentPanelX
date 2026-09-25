"""Selection and formatting for small, one-way Runtime notifications."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from loguru import logger

from agentplanex.domains.execution_event import ExecutionEvent, ExecutionEventType

_MAX_FIELD_LENGTH = 120
_MAX_FAILURE_LENGTH = 180
_PATH_PATTERN = re.compile(r"(?<!\w)(?:[A-Za-z]:[\\/]|/|~/)[^\s,;)]*")
_URL_PATTERN = re.compile(r"https?://[^\s,;)]*", re.IGNORECASE)
_SECRET_PATTERN = re.compile(
    r"(?i)(api[_ -]?key|authorization|password|secret|token)\s*[:=]\s*[^\s,;)]*"
)


class NotificationSender(Protocol):
    """The small boundary used by the notification service and its tests."""

    def send(self, text: str) -> None:
        """Deliver one already-rendered notification."""


@dataclass(frozen=True, slots=True)
class NotificationService:
    """Render only selected Runtime facts and send them best effort."""

    sender: NotificationSender

    def __call__(self, event: ExecutionEvent) -> None:
        text = self.render(event)
        if text is None:
            return
        try:
            self.sender.send(text)
        except Exception:
            logger.exception(
                "Runtime notification delivery failed triage_id={} event_type={}",
                event.triage_id,
                event.event_type.value,
            )

    @staticmethod
    def render(event: ExecutionEvent) -> str | None:
        """Return a bounded message for a user-actionable event, if any."""
        payload = event.payload
        details: list[str]
        if event.event_type is ExecutionEventType.PLAN_APPROVAL_REQUESTED:
            details = [f"Plan: {_field(payload, 'subject_digest')}"]
            return _message(
                event.triage_id,
                created_at=event.created_at,
                title="Plan approval required",
                status="WAITING_FOR_HUMAN",
                action="Approve or reject the Plan",
                details=details,
            )
        if event.event_type is ExecutionEventType.FIRST_RUN_APPROVAL_REQUESTED:
            details = [
                f"Snapshot: {_field(payload, 'snapshot_id')}",
                f"Milestone: {_field(payload, 'milestone_key')}",
            ]
            return _message(
                event.triage_id,
                created_at=event.created_at,
                title="First Run approval required",
                status="WAITING_FOR_HUMAN",
                action="Approve or reject the first Run",
                details=details,
            )
        if event.event_type is ExecutionEventType.BLOCKED_RUN_APPROVAL_REQUESTED:
            details = [
                f"Snapshot: {_field(payload, 'snapshot_id')}",
                f"Milestone: {_field(payload, 'milestone_key')}",
                f"Run: {_field(payload, 'failed_run_id')}",
                f"Stage: {_field(payload, 'failed_stage_key')}",
            ]
            return _message(
                event.triage_id,
                created_at=event.created_at,
                title="Blocked Run approval required",
                status="WAITING_FOR_HUMAN",
                action="Approve or reject the retry",
                details=details,
            )
        if event.event_type is ExecutionEventType.OWNER_ACTIVATION_FAILED:
            details = [
                f"Activation: {_field(payload, 'activation_id')}",
                f"Task: {_field(payload, 'task_type')}",
                f"Reason: {_failure_field(payload, 'failure')}",
            ]
            return _message(
                event.triage_id,
                created_at=event.created_at,
                title="Owner activation failed",
                status="BLOCKED",
                action="Inspect the Runtime and intervene",
                details=details,
            )
        if event.event_type is ExecutionEventType.STAGE_RUN_FAILED:
            details = [
                f"Run: {_field(payload, 'run_id')}",
                f"Milestone: {_field(payload, 'milestone_key')}",
                f"Stage: {_field(payload, 'stage_key')}",
                f"Stage run: {_field(payload, 'stage_run_id')}",
            ]
            return _message(
                event.triage_id,
                created_at=event.created_at,
                title="Stage execution failed",
                status="BLOCKED",
                action="Inspect the failed Stage",
                details=details,
            )
        if event.event_type is ExecutionEventType.AUTO_TAKEOVER_FAILED:
            details = [
                f"Run: {_field(payload, 'run_id')}",
                f"Reason: {_failure_field(payload, 'error')}",
            ]
            return _message(
                event.triage_id,
                created_at=event.created_at,
                title="AutoTakeover failed",
                status="BLOCKED",
                action="Inspect the blocked Run and intervene",
                details=details,
            )
        if event.event_type is ExecutionEventType.TRIAGE_DEVELOPMENT_COMPLETED:
            details = [
                f"Candidate: {_field(payload, 'candidate_commit_sha')}",
                f"Snapshot: {_field(payload, 'snapshot_id')}",
            ]
            return _message(
                event.triage_id,
                created_at=event.created_at,
                title="Feature development completed",
                status="COMPLETED",
                action="Review the accepted Candidate",
                details=details,
            )
        return None


def _message(
    triage_id: str,
    *,
    created_at: datetime,
    title: str,
    status: str,
    action: str,
    details: list[str],
) -> str:
    return "\n".join(
        [
            f"[AgentPlaneX] {title}",
            f"Feature: {_bounded(triage_id)}",
            f"Status: {status}",
            f"Action: {action}",
            f"Time: {created_at.isoformat(timespec='seconds')}",
            *details,
        ]
    )


def _field(payload: dict[str, object], name: str) -> str:
    value = payload.get(name)
    if value is None:
        return "unknown"
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        return _bounded(str(value)) or "unknown"
    return "unknown"


def _failure_field(payload: dict[str, object], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str):
        return "unknown"
    return _bounded(_redact(value), limit=_MAX_FAILURE_LENGTH) or "unknown"


def _bounded(value: str, *, limit: int = _MAX_FIELD_LENGTH) -> str:
    return " ".join(value.split())[:limit]


def _redact(value: str) -> str:
    redacted = _SECRET_PATTERN.sub(r"\1=<redacted>", value)
    redacted = _URL_PATTERN.sub("<url>", redacted)
    return _PATH_PATTERN.sub("<path>", redacted)
