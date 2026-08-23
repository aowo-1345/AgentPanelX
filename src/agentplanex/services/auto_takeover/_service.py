"""Runtime-owned orchestration for provisional BLOCKED transitions."""

from __future__ import annotations

import sqlite3
import sys
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Literal, Protocol

from loguru import logger

from agentplanex.domains.execution_event import ExecutionEvent, ExecutionEventType
from agentplanex.domains.workspace import FeatureBinding
from agentplanex.infrastructure.agent_workspace import AgentWorkspaceError
from agentplanex.infrastructure.sqlite import SQLiteDatabase
from agentplanex.infrastructure.sqlite.repositories import (
    SQLiteAutoTakeoverRepository,
    SQLiteExecutionEventRepository,
    SQLiteProjectRuntimeStateRepository,
    SQLiteStageRunRepository,
)
from agentplanex.services.auto_takeover._operation import (
    AutoTakeoverOutput,
    AutoTakeoverPayload,
)
from agentplanex.services.auto_takeover.models import (
    AutoTakeoverSnapshot,
    TakeoverAttempt,
    TakeoverRun,
    TakeoverStatus,
)
from agentplanex.services.delivery.models import StageRunStatus
from agentplanex.services.external_agent_runtime import (
    ExternalAgentRequest,
    ExternalAgentRuntime,
    ManagedAgentScope,
)

type ExternalRuntimeFactory = Callable[[Path], ExternalAgentRuntime]
type ScheduleDrive = Callable[[FeatureBinding], None]


class AutoTakeoverPort(Protocol):
    def event_watermark(self, binding: FeatureBinding) -> int: ...

    def after_drive_released(
        self,
        binding: FeatureBinding,
        *,
        after_event_id: int,
    ) -> None: ...

    def stop_accepting(self) -> None: ...

    def close(self) -> None: ...


@dataclass(slots=True)
class AutoTakeoverService:
    """Start, validate, and terminalize one fenced takeover per BLOCKED event."""

    external_runtime_factory: ExternalRuntimeFactory
    schedule_drive: ScheduleDrive
    settings_path: Path | None = None
    budget_seconds: float = 1800.0
    max_parallel_features: int = 4
    _runs: SQLiteAutoTakeoverRepository = field(
        default_factory=SQLiteAutoTakeoverRepository,
        repr=False,
    )
    _events: SQLiteExecutionEventRepository = field(
        default_factory=SQLiteExecutionEventRepository,
        repr=False,
    )
    _states: SQLiteProjectRuntimeStateRepository = field(
        default_factory=SQLiteProjectRuntimeStateRepository,
        repr=False,
    )
    _stage_runs: SQLiteStageRunRepository = field(
        default_factory=SQLiteStageRunRepository,
        repr=False,
    )
    _executor: ThreadPoolExecutor = field(init=False, repr=False)
    _futures: set[Future[None]] = field(default_factory=set, init=False, repr=False)
    _guard: Lock = field(default_factory=Lock, init=False, repr=False)
    _accepting: bool = field(default=True, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.budget_seconds <= 0:
            raise ValueError("AutoTakeover budget must be positive")
        if self.budget_seconds > 1800:
            raise ValueError("AutoTakeover budget cannot exceed 1800 seconds")
        if self.max_parallel_features <= 0:
            raise ValueError("AutoTakeover parallel Feature limit must be positive")
        if self.settings_path is not None:
            self.settings_path = self.settings_path.resolve()
            if not self.settings_path.is_file():
                raise ValueError("AutoTakeover Runtime settings file is unavailable")
        self._executor = ThreadPoolExecutor(
            max_workers=self.max_parallel_features,
            thread_name_prefix="agentplanex-auto-takeover",
        )

    def event_watermark(self, binding: FeatureBinding) -> int:
        database = SQLiteDatabase.for_project(binding.worktree_path)
        with database.read_only_connection() as connection:
            events = self._events.list_by_triage_id(connection, binding.triage_id)
        if not events:
            return 0
        return events[-1].event_id or 0

    def after_drive_released(
        self,
        binding: FeatureBinding,
        *,
        after_event_id: int,
    ) -> None:
        """Persist and schedule only a new real IN_PROGRESS -> BLOCKED event."""
        with self._guard:
            if not self._accepting:
                return
        database = SQLiteDatabase.for_project(binding.worktree_path)
        with database.transaction() as connection:
            trigger = self._new_blocked_event(connection, binding, after_event_id)
            if trigger is None or trigger.event_id is None:
                return
            started = self._runs.begin(
                connection,
                triage_id=binding.triage_id,
                trigger_event_id=trigger.event_id,
            )
            if started is None:
                return
            run, attempt = started
            self._insert_event(
                connection,
                binding.triage_id,
                ExecutionEventType.AUTO_TAKEOVER_STARTED,
                {
                    "run_id": run.run_id,
                    "attempt_id": attempt.attempt_id,
                    "attempt": attempt.ordinal,
                    "trigger_event_id": trigger.event_id,
                },
            )
        with self._guard:
            if not self._accepting:
                self._fail(
                    binding,
                    run,
                    "AutoTakeover stopped before its worker could start",
                )
                return
            future = self._executor.submit(self._execute, binding, trigger, run, attempt)
            self._futures.add(future)
        future.add_done_callback(self._finished)

    def snapshot(self, binding: FeatureBinding) -> AutoTakeoverSnapshot | None:
        database = SQLiteDatabase.for_project(binding.worktree_path)
        with database.read_only_connection() as connection:
            run = self._runs.latest(connection, binding.triage_id)
        if run is None:
            return None
        phase: Literal["recovering", "recovered", "blocked", "failed"]
        if run.status is TakeoverStatus.RUNNING:
            phase = "recovering"
        elif run.status is TakeoverStatus.YES:
            phase = "recovered"
        elif run.status is TakeoverStatus.NO:
            phase = "blocked"
        else:
            phase = "failed"
        return AutoTakeoverSnapshot(
            trigger_event_id=run.trigger_event_id,
            phase=phase,
            attribution=run.attribution,
            error=run.error,
        )

    def stop_accepting(self) -> None:
        with self._guard:
            self._accepting = False

    def close(self) -> None:
        self.stop_accepting()
        self._executor.shutdown(wait=True)

    def _execute(
        self,
        binding: FeatureBinding,
        trigger: ExecutionEvent,
        run: TakeoverRun,
        attempt: TakeoverAttempt,
    ) -> None:
        deadline = time.monotonic() + self.budget_seconds
        correction: str | None = None
        current = attempt
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("AutoTakeover exhausted its 1800-second budget")
                result = (
                    self.external_runtime_factory(binding.worktree_path)
                    .invoke(
                        ExternalAgentRequest(
                            agent_key="auto_takeover",
                            operation_key="auto_takeover_v1",
                            request_key=current.attempt_id,
                            scope=ManagedAgentScope(triage_id=binding.triage_id),
                            payload=self._payload(
                                binding,
                                trigger,
                                current,
                                remaining,
                                correction,
                            ),
                        )
                    )
                    .output
                )
                if not isinstance(result, AutoTakeoverOutput):
                    raise TypeError("AutoTakeover Operation returned an unexpected result")
                if time.monotonic() >= deadline:
                    raise TimeoutError("AutoTakeover exhausted its 1800-second budget")
                result_error: str | None
                if result.attribution is not None:
                    try:
                        self.external_runtime_factory(
                            binding.worktree_path
                        ).workspaces.resolve_descriptor(result.attribution)
                    except (AgentWorkspaceError, OSError, ValueError):
                        result_error = (
                            "NO Attribution Artifact failed integrity validation"
                        )
                    else:
                        result_error = None
                else:
                    result_error = None
                database = SQLiteDatabase.for_project(binding.worktree_path)
                with database.transaction() as connection:
                    mismatch = result_error or self._result_mismatch(
                        connection,
                        binding,
                        run,
                        current,
                        result,
                    )
                    if mismatch is None:
                        self._complete(connection, binding, run, result)
                    elif current.ordinal == 2:
                        raise ValueError(
                            "AutoTakeover result remained inconsistent after correction: "
                            + mismatch
                        )
                    else:
                        current = self._runs.correct(connection, run.run_id, mismatch)
                        self._insert_event(
                            connection,
                            binding.triage_id,
                            ExecutionEventType.AUTO_TAKEOVER_STARTED,
                            {
                                "run_id": run.run_id,
                                "attempt_id": current.attempt_id,
                                "attempt": current.ordinal,
                                "trigger_event_id": run.trigger_event_id,
                                "correction": mismatch,
                            },
                        )
                if mismatch is None:
                    if result.decision == "YES":
                        self.schedule_drive(binding)
                    return
                correction = mismatch
        except Exception as error:
            logger.exception(
                "AutoTakeover failed triage_id={} event_id={}",
                binding.triage_id,
                run.trigger_event_id,
            )
            self._fail(binding, run, str(error))

    def _payload(
        self,
        binding: FeatureBinding,
        trigger: ExecutionEvent,
        attempt: TakeoverAttempt,
        remaining: float,
        correction: str | None,
    ) -> AutoTakeoverPayload:
        if attempt.fence_token is None:
            raise ValueError("AutoTakeover Attempt has no active fence")
        cli_prefix = (
            sys.executable,
            "-m",
            "agentplanex.app_cli",
        )
        if self.settings_path is not None:
            cli_prefix += ("--config", str(self.settings_path))
        control_prefix = (
            *cli_prefix,
            "auto-control",
            "--cwd",
            str(binding.worktree_path.resolve()),
            "--takeover-fence",
            attempt.fence_token,
            "--print",
        )
        owner_prefix = (
            *cli_prefix,
            "auto-owner-fork",
            "--cwd",
            str(binding.worktree_path.resolve()),
        )
        if attempt.ordinal not in {1, 2}:
            raise ValueError("AutoTakeover Attempt ordinal is invalid")
        ordinal: Literal[1, 2] = 1 if attempt.ordinal == 1 else 2
        return AutoTakeoverPayload(
            triage_id=binding.triage_id,
            trigger_event_id=run_event_id(trigger),
            blocked_event=trigger.payload,
            attempt_id=attempt.attempt_id,
            ordinal=ordinal,
            fence_token=attempt.fence_token,
            remaining_seconds=max(0.001, remaining),
            control_command_prefix=control_prefix,
            owner_fork_command=owner_prefix,
            correction=correction,
        )

    def _result_mismatch(
        self,
        connection: sqlite3.Connection,
        binding: FeatureBinding,
        run: TakeoverRun,
        attempt: TakeoverAttempt,
        result: AutoTakeoverOutput,
    ) -> str | None:
        saved_run = self._runs.get(connection, run.run_id)
        active_attempt = self._runs.get_active_attempt(connection, run.run_id)
        if saved_run is None or saved_run.status is not TakeoverStatus.RUNNING:
            return "AutoTakeover Run is no longer active"
        if (
            active_attempt is None
            or active_attempt.attempt_id != attempt.attempt_id
            or active_attempt.fence_token != attempt.fence_token
        ):
            return "AutoTakeover Attempt fence is no longer current"
        state = self._states.get(connection, binding.triage_id)
        active = self._stage_runs.get_active(connection, binding.triage_id)
        if state is None:
            return "Runtime State disappeared"
        if result.decision == "YES":
            if state.status != "IN_PROGRESS":
                return f"YES requires IN_PROGRESS, observed {state.status}"
            if active is None or active.status is not StageRunStatus.QUEUED:
                return "YES requires exactly one untouched QUEUED StageRun"
            return None
        if state.status != "BLOCKED":
            return f"NO requires BLOCKED, observed {state.status}"
        if result.attribution is None:
            return "NO requires a published Attribution Artifact"
        return None

    def _complete(
        self,
        connection: sqlite3.Connection,
        binding: FeatureBinding,
        run: TakeoverRun,
        result: AutoTakeoverOutput,
    ) -> None:
        status = TakeoverStatus(result.decision)
        self._runs.complete(
            connection,
            run.run_id,
            status,
            attribution=result.attribution,
        )
        self._insert_event(
            connection,
            binding.triage_id,
            ExecutionEventType.AUTO_TAKEOVER_COMPLETED,
            {
                "run_id": run.run_id,
                "trigger_event_id": run.trigger_event_id,
                "decision": result.decision,
                "attribution": (
                    result.attribution.uri if result.attribution is not None else None
                ),
            },
        )

    def _fail(
        self,
        binding: FeatureBinding,
        run: TakeoverRun,
        error: str,
    ) -> None:
        database = SQLiteDatabase.for_project(binding.worktree_path)
        bounded = " ".join(error.split())[:2_000] or "AutoTakeover failed"
        with database.transaction() as connection:
            active = self._runs.get(connection, run.run_id)
            if active is None or active.status is not TakeoverStatus.RUNNING:
                return
            self._runs.complete(
                connection,
                run.run_id,
                TakeoverStatus.FAILED,
                error=bounded,
            )
            self._insert_event(
                connection,
                binding.triage_id,
                ExecutionEventType.AUTO_TAKEOVER_FAILED,
                {
                    "run_id": run.run_id,
                    "trigger_event_id": run.trigger_event_id,
                    "error": bounded,
                },
            )

    def _new_blocked_event(
        self,
        connection: sqlite3.Connection,
        binding: FeatureBinding,
        after_event_id: int,
    ) -> ExecutionEvent | None:
        events = self._events.list_by_triage_id(connection, binding.triage_id)
        candidates = [
            event
            for event in events
            if (event.event_id or 0) > after_event_id and _is_blocked_transition(event)
        ]
        if not candidates:
            return None
        state = self._states.get(connection, binding.triage_id)
        if state is None or state.status != "BLOCKED":
            return None
        return candidates[-1]

    def _insert_event(
        self,
        connection: sqlite3.Connection,
        triage_id: str,
        event_type: ExecutionEventType,
        payload: dict[str, object],
    ) -> None:
        self._events.insert(
            connection,
            ExecutionEvent(
                triage_id=triage_id,
                event_type=event_type,
                payload=payload,
            ),
        )

    def _finished(self, future: Future[None]) -> None:
        with self._guard:
            self._futures.discard(future)


def _is_blocked_transition(event: ExecutionEvent) -> bool:
    if event.event_type is not ExecutionEventType.RUNTIME_CONTEXT_UPDATED:
        return False
    changes = event.payload.get("changes")
    if not isinstance(changes, dict):
        return False
    status = changes.get("status")
    return isinstance(status, dict) and status == {
        "from": "IN_PROGRESS",
        "to": "BLOCKED",
    }


def run_event_id(event: ExecutionEvent) -> int:
    if event.event_id is None:
        raise ValueError("AutoTakeover trigger has no persisted event ID")
    return event.event_id
