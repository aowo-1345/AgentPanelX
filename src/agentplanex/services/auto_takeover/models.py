"""Durable AutoTakeover business values."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal

from agentplanex.domains.artifact import ArtifactDescriptor


class TakeoverStatus(StrEnum):
    RUNNING = "RUNNING"
    YES = "YES"
    NO = "NO"
    FAILED = "FAILED"


class AttemptStatus(StrEnum):
    RUNNING = "RUNNING"
    INVALID = "INVALID"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class TakeoverRun:
    run_id: str
    triage_id: str
    trigger_event_id: int
    status: TakeoverStatus
    decision: str | None
    attribution: ArtifactDescriptor | None
    error: str | None
    started_at: datetime
    finished_at: datetime | None
    issue_number: int | None
    issue_url: str | None


@dataclass(frozen=True, slots=True)
class TakeoverAttempt:
    attempt_id: str
    run_id: str
    ordinal: int
    status: AttemptStatus
    fence_token: str | None
    error: str | None
    started_at: datetime
    finished_at: datetime | None


@dataclass(frozen=True, slots=True)
class AutoTakeoverSnapshot:
    trigger_event_id: int
    phase: Literal["recovering", "recovered", "blocked", "failed"]
    attribution: ArtifactDescriptor | None = None
    error: str | None = None

    @property
    def attribution_required(self) -> bool:
        return self.phase == "blocked" and self.attribution is None
