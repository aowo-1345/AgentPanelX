"""Small in-process fan-out buffer for observing one active Stage turn."""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass
from threading import RLock
from typing import Any


@dataclass(frozen=True, slots=True)
class StageOutputChunk:
    sequence: int
    data: str


@dataclass(slots=True)
class _StageOutputSession:
    active: bool = True
    next_sequence: int = 1
    chunks: deque[StageOutputChunk] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.chunks = deque(maxlen=500)


class StageOutputObserver:
    """Keep a bounded, process-local stream for an active StageRun."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._sessions: dict[str, _StageOutputSession] = {}

    def begin(self, stage_run_id: str) -> None:
        with self._lock:
            existing = self._sessions.get(stage_run_id)
            if existing is None or not existing.active:
                self._sessions[stage_run_id] = _StageOutputSession()

    def publish_event(self, stage_run_id: str, event: Any) -> None:
        method = getattr(event, "method", "codex/event")
        payload = getattr(event, "payload", None)
        for attribute in ("delta", "text", "message"):
            value = getattr(payload, attribute, None)
            if isinstance(value, str) and value:
                self.publish(stage_run_id, value)
                return
        try:
            rendered = json.dumps(
                {"method": method, "payload": payload},
                default=str,
                ensure_ascii=False,
                sort_keys=True,
            )
        except (TypeError, ValueError):
            rendered = f"[{method}] {payload!r}"
        self.publish(stage_run_id, f"{rendered}\r\n")

    def publish(self, stage_run_id: str, data: str) -> None:
        if not data:
            return
        with self._lock:
            session = self._sessions.get(stage_run_id)
            if session is None:
                session = _StageOutputSession()
                self._sessions[stage_run_id] = session
            chunk = StageOutputChunk(sequence=session.next_sequence, data=data)
            session.next_sequence += 1
            session.chunks.append(chunk)

    def finish(self, stage_run_id: str) -> None:
        with self._lock:
            session = self._sessions.get(stage_run_id)
            if session is not None:
                session.active = False

    def close(self, stage_run_id: str) -> None:
        """Drop one finished session; repeated cleanup is intentionally safe."""
        with self._lock:
            self._sessions.pop(stage_run_id, None)

    def close_all(self) -> None:
        with self._lock:
            self._sessions.clear()

    def is_active(self, stage_run_id: str) -> bool:
        with self._lock:
            session = self._sessions.get(stage_run_id)
            return session is not None and session.active

    def read_since(
        self,
        stage_run_id: str,
        after_sequence: int = 0,
    ) -> tuple[tuple[StageOutputChunk, ...], bool]:
        with self._lock:
            session = self._sessions.get(stage_run_id)
            if session is None:
                return (), False
            chunks = tuple(
                chunk for chunk in session.chunks if chunk.sequence > after_sequence
            )
            return chunks, session.active
