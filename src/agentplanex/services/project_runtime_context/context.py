"""Single ownership point for Feature identity, State, and write coordination."""

import fcntl
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from threading import RLock, get_ident
from typing import BinaryIO
from uuid import uuid4

from agentplanex.domains.execution_event import (
    ExecutionEvent,
    ProjectOwnerTask,
    RuntimeContextChangeReason,
)
from agentplanex.domains.owner_activation import (
    OwnerActivation,
    OwnerActivationStatus,
)
from agentplanex.domains.project_runtime_state import ProjectRuntimeState
from agentplanex.infrastructure.sqlite import SQLiteDatabase
from agentplanex.infrastructure.sqlite.repositories import (
    SQLiteProjectRuntimeStateRepository,
)
from agentplanex.project_owner_agent.contracts import (
    Action,
    AgentExit,
    AgentExitStatus,
    ToolExecutionResult,
)
from agentplanex.project_runtime.errors import FeatureBusyError
from agentplanex.services.event_bus import EventBus
from agentplanex.services.project_runtime_context._activation import (
    ActivationDriveResult,
    OwnerWorkState,
    ToolActivationDriveResult,
    _manual_failure,
    _now,
    _OwnerActivationLifecycle,
    _unhandled_exit,
)
from agentplanex.services.project_runtime_context._owner import _OwnerRuntime
from agentplanex.services.project_runtime_context._state import (
    StateMutation,
    apply_state_transition,
)
from agentplanex.services.project_runtime_context.contracts import RuntimeToolExecutor


@dataclass(slots=True)
class ProjectRuntimeContext:
    """Own one Feature's mutable execution boundary and persisted State."""

    project_path: Path
    database: SQLiteDatabase
    event_bus: EventBus
    _states: SQLiteProjectRuntimeStateRepository = field(
        default_factory=SQLiteProjectRuntimeStateRepository
    )
    _lock: RLock = field(default_factory=RLock, init=False, repr=False)
    _operation_owner: int | None = field(default=None, init=False, repr=False)
    _operation_depth: int = field(default=0, init=False, repr=False)
    _lock_file: BinaryIO | None = field(default=None, init=False, repr=False)
    _cached_state: ProjectRuntimeState | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _owner_runtime: _OwnerRuntime | None = field(default=None, init=False, repr=False)
    _tool_executor: RuntimeToolExecutor | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _activation: _OwnerActivationLifecycle = field(
        default_factory=_OwnerActivationLifecycle,
        init=False,
        repr=False,
    )
    _sealed: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        self.project_path = self.project_path.resolve()
        database_project_path = self.database.path.parent.parent.resolve()
        if database_project_path != self.project_path:
            raise ValueError("Runtime Context database does not belong to project path")

    def _complete(
        self,
        *,
        owner_runtime: _OwnerRuntime,
        tool_executor: RuntimeToolExecutor,
    ) -> None:
        """Install the private execution graph atomically during composition."""
        if (
            self._sealed
            or self._owner_runtime is not None
            or self._tool_executor is not None
        ):
            raise RuntimeError("Project Runtime Context composition is already complete")
        self._owner_runtime = owner_runtime
        self._tool_executor = tool_executor
        self._sealed = True

    @contextmanager
    def operation(self) -> Iterator[None]:
        """Fail fast when another Runtime already owns this Feature."""
        self._require_sealed()
        if not self._lock.acquire(blocking=False):
            raise FeatureBusyError(str(self.project_path))
        current_thread = get_ident()
        outermost = self._operation_depth == 0
        try:
            if outermost:
                lock_path = self.database.path.parent / "runtime.lock"
                lock_path.parent.mkdir(parents=True, exist_ok=True)
                lock_file = lock_path.open("a+b")
                try:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as error:
                    lock_file.close()
                    raise FeatureBusyError(str(self.project_path)) from error
                self._lock_file = lock_file
                self._operation_owner = current_thread
                self._cached_state = None
            elif self._operation_owner != current_thread:
                raise FeatureBusyError(str(self.project_path))
            self._operation_depth += 1
            yield
        finally:
            if self._operation_depth > 0:
                self._operation_depth -= 1
            if outermost:
                self._cached_state = None
                self._operation_owner = None
                held_lock_file = self._lock_file
                self._lock_file = None
                if held_lock_file is not None:
                    fcntl.flock(held_lock_file.fileno(), fcntl.LOCK_UN)
                    held_lock_file.close()
            self._lock.release()

    def initialize(self) -> ProjectRuntimeState:
        """Atomically create or restore the sole State and Owner identity."""
        with self.operation(), self.database.transaction() as connection:
            states = self._states.list_all(connection)
            if len(states) > 1:
                raise RuntimeError("Project contains more than one Runtime State")
            if states:
                state = states[0]
                self._owner().restore_identity(connection, state)
            else:
                owner_count = connection.execute(
                    "SELECT COUNT(*) FROM project_owner_agent"
                ).fetchone()[0]
                if owner_count:
                    raise RuntimeError("Project Owner identity exists without Runtime State")
                state = ProjectRuntimeState(triage_id=uuid4().hex)
                self._states.insert(connection, state)
                self._owner().create_identity(connection, state)
        return state

    def state(self) -> ProjectRuntimeState:
        """Restore the sole initialized State without creating it."""
        with self.operation():
            return self._current_state()

    def transition(
        self,
        *,
        reason: RuntimeContextChangeReason,
        mutate: StateMutation,
    ) -> ProjectRuntimeState:
        """Commit one State transition and publish its evidence afterwards."""
        with self.operation(), self.transaction() as transaction:
            return transaction.transition(reason=reason, mutate=mutate)

    def owner_work(self) -> OwnerWorkState:
        """Describe whether the sole Owner work item can run automatically."""
        with self.operation(), self.transaction() as transaction:
            return transaction.owner_work()

    def reconcile_interrupted_owner(
        self,
        *,
        finished_at: datetime,
        failure: str,
    ) -> bool:
        """Atomically fail unfinished Owner work and block the Feature."""
        with self.operation(), self.transaction() as transaction:
            failed = transaction.fail_interrupted_owner(
                finished_at=finished_at,
                failure=failure,
            )
            if not failed:
                return False
            transaction.transition(
                reason=RuntimeContextChangeReason.INTERRUPTED_WORK_FAILED,
                mutate=_block_after_owner_failure,
            )
        return True

    def drive_owner(self) -> ActivationDriveResult:
        """Claim, run, and terminalize at most one MODEL Owner activation."""
        with self.operation():
            with self.transaction() as transaction:
                state = transaction.state()
                activation = self._activation.claim_for_model(
                    transaction.connection,
                    state.triage_id,
                    started_at=_now(),
                )
                if activation is not None:
                    transaction._stage_event(self._activation.entered_event(activation))
            if activation is None:
                return ActivationDriveResult(activation=None, exit=None)

            try:
                result = self._owner().run_activation(
                    self._reload_state(),
                    activation,
                )
            except Exception as error:
                result = _unhandled_exit(error)
            finalized = self._finish_owner(activation, result)
            return ActivationDriveResult(activation=finalized, exit=result)

    def drive_owner_tool(self, action: Action) -> ToolActivationDriveResult:
        """Execute one supplied Tool Action inside the current Owner activation."""
        with self.operation():
            with self.transaction() as transaction:
                state = transaction.state()
                claim = self._activation.claim_for_tool(
                    transaction.connection,
                    state.triage_id,
                    started_at=_now(),
                )
                if claim.started:
                    transaction._stage_event(self._activation.entered_event(claim.activation))
            try:
                tool_result = self._owner().execute_activation_action(
                    self._reload_state(),
                    claim.activation,
                    action,
                )
            except Exception as error:
                return self._fail_tool_step(claim.activation, claim.started, error)

            result_exit = tool_result.exit
            if result_exit is not None:
                activation = self._finish_owner(claim.activation, result_exit)
            else:
                with self.transaction() as transaction:
                    activation = self._activation.release_tool(
                        transaction.connection,
                        claim.activation,
                    )
            return ToolActivationDriveResult(
                activation=activation,
                started=claim.started,
                tool_result=tool_result,
                exit=result_exit,
            )

    def reply_owner(self, content: str) -> ToolActivationDriveResult:
        """Append a manual reply and complete the current TOOL activation atomically."""
        with self.operation():
            with self.transaction() as transaction:
                state = transaction.state()
                claim = self._activation.claim_for_tool(
                    transaction.connection,
                    state.triage_id,
                    started_at=_now(),
                )
                if claim.started:
                    transaction._stage_event(self._activation.entered_event(claim.activation))
            try:
                with self.transaction() as transaction:
                    result = self._owner().append_reply(
                        transaction.connection,
                        transaction.state(),
                        claim.activation,
                        content,
                    )
                    activation = self._finish_owner_in_transaction(
                        transaction,
                        claim.activation,
                        result,
                    )
            except Exception as error:
                return self._fail_tool_step(claim.activation, claim.started, error)
            return ToolActivationDriveResult(
                activation=activation,
                started=claim.started,
                tool_result=None,
                exit=result,
            )

    def fail_owner(self, reason: str) -> ToolActivationDriveResult:
        """Explicitly fail the current waiting or interrupted TOOL activation."""
        result = _manual_failure(reason)
        with self.operation(), self.transaction() as transaction:
            state = transaction.state()
            claim = self._activation.claim_for_tool(
                transaction.connection,
                state.triage_id,
                started_at=_now(),
                allow_running=True,
            )
            if claim.started:
                transaction._stage_event(self._activation.entered_event(claim.activation))
            activation = self._finish_owner_in_transaction(
                transaction,
                claim.activation,
                result,
            )
        return ToolActivationDriveResult(
            activation=activation,
            started=claim.started,
            tool_result=None,
            exit=result,
        )

    def execute_tool(self, action: Action) -> ToolExecutionResult:
        """Execute a naked Tool action without changing Owner history."""
        with self.operation():
            with self.transaction() as transaction:
                if transaction.owner_work() is not OwnerWorkState.IDLE:
                    raise ValueError(
                        "Project Owner has an unfinished activation; use drive tool "
                        "so the Action stays bound to it"
                    )
            executor = self._tool_executor
            if executor is None:
                raise RuntimeError("Project Runtime Context Tool Executor is not bound")
            return executor(self._reload_state(), action)

    def _finish_owner(
        self,
        activation: OwnerActivation,
        result: AgentExit,
    ) -> OwnerActivation:
        with self.transaction() as transaction:
            return self._finish_owner_in_transaction(
                transaction,
                activation,
                result,
            )

    def _finish_owner_in_transaction(
        self,
        transaction: "ProjectRuntimeTransaction",
        activation: OwnerActivation,
        result: AgentExit,
    ) -> OwnerActivation:
        finalized = self._activation.finish(
            transaction.connection,
            activation,
            result,
            finished_at=_now(),
        )
        if finalized.status is OwnerActivationStatus.FAILED:
            transaction.transition(
                reason=RuntimeContextChangeReason.OWNER_ACTIVATION_FAILED,
                mutate=_block_after_owner_failure,
            )
        transaction._stage_event(self._activation.exited_event(finalized, result))
        return finalized

    def _fail_tool_step(
        self,
        activation: OwnerActivation,
        started: bool,
        error: Exception,
    ) -> ToolActivationDriveResult:
        result = _unhandled_exit(error)
        failed = self._finish_owner(activation, result)
        return ToolActivationDriveResult(
            activation=failed,
            started=started,
            tool_result=None,
            exit=result,
        )

    def _set_owner_activation_initial_summary(
        self,
        connection: sqlite3.Connection,
        activation_id: str,
        summary_id: str,
    ) -> None:
        """Private checkpoint writer supplied only to the private Owner runtime."""
        self._activation.set_initial_summary(
            connection,
            activation_id,
            summary_id,
        )

    @contextmanager
    def transaction(self) -> Iterator["ProjectRuntimeTransaction"]:
        """Let Runtime collaborators share one SQLite atomic write."""
        self._require_operation()
        transaction = ProjectRuntimeTransaction(self)
        with self.database.transaction() as connection:
            transaction._connection = connection
            try:
                yield transaction
            except BaseException:
                transaction._discard()
                raise
        transaction._commit()

    def _current_state(self) -> ProjectRuntimeState:
        self._require_operation()
        if self._cached_state is not None:
            return self._cached_state
        with self.database.connection() as connection:
            states = self._states.list_all(connection)
            if not states:
                raise LookupError("Project Runtime is not initialized")
            if len(states) > 1:
                raise RuntimeError("Project contains more than one Runtime State")
            self._owner().restore_identity(connection, states[0])
        self._cached_state = states[0]
        return states[0]

    def _reload_state(self) -> ProjectRuntimeState:
        """Bypass the operation cache at Tool and Activation execution boundaries."""
        self._require_operation()
        with self.database.connection() as connection:
            states = self._states.list_all(connection)
            if not states:
                raise LookupError("Project Runtime is not initialized")
            if len(states) > 1:
                raise RuntimeError("Project contains more than one Runtime State")
            self._owner().restore_identity(connection, states[0])
        self._cached_state = states[0]
        return states[0]

    def _owner(self) -> _OwnerRuntime:
        if self._owner_runtime is None:
            raise RuntimeError("Project Runtime Context Owner is not bound")
        return self._owner_runtime

    def _require_operation(self) -> None:
        if self._operation_depth == 0 or self._operation_owner != get_ident():
            raise RuntimeError("Project Runtime Context operation is not active")

    def _require_sealed(self) -> None:
        if not self._sealed:
            raise RuntimeError("Project Runtime Context composition is not sealed")


@dataclass(slots=True)
class ProjectRuntimeTransaction:
    """A Context-owned transaction with staged cache and Timeline effects."""

    _context: ProjectRuntimeContext
    _connection: sqlite3.Connection | None = field(default=None, init=False)
    _state: ProjectRuntimeState | None = field(default=None, init=False)
    _events: list[ExecutionEvent] = field(default_factory=list, init=False)

    @property
    def connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("Project Runtime transaction is not active")
        return self._connection

    def state(self) -> ProjectRuntimeState:
        if self._state is not None:
            return self._state
        states = self._context._states.list_all(self.connection)
        if not states:
            raise LookupError("Project Runtime is not initialized")
        if len(states) > 1:
            raise RuntimeError("Project contains more than one Runtime State")
        self._context._owner().restore_identity(self.connection, states[0])
        self._state = states[0]
        return states[0]

    def transition(
        self,
        *,
        reason: RuntimeContextChangeReason,
        mutate: StateMutation,
    ) -> ProjectRuntimeState:
        current = self.state()
        updated, event = apply_state_transition(
            self.connection,
            self._context._states,
            current,
            reason=reason,
            mutate=mutate,
        )
        self._state = updated
        if event is not None:
            self._events.append(event)
        return updated

    def submit_owner_input(
        self,
        owner_input: ProjectOwnerTask,
    ) -> OwnerActivation:
        """Persist one Owner Input and its unique Activation atomically."""
        state = self.state()
        if (
            self._context._activation.work_state(
                self.connection,
                state.triage_id,
            )
            is not OwnerWorkState.IDLE
        ):
            raise ValueError("Project Owner already has an unfinished activation")
        message_id, summary_id = self._context._owner().append_task(
            self.connection,
            state,
            owner_input,
        )
        return self._context._activation.submit_input(
            self.connection,
            triage_id=state.triage_id,
            owner_input=owner_input,
            message_id=message_id,
            summary_id=summary_id,
        )

    def owner_work(self) -> OwnerWorkState:
        """Return the scheduling state of the Context-owned Owner mailbox."""
        state = self.state()
        return self._context._activation.work_state(
            self.connection,
            state.triage_id,
        )

    def fail_interrupted_owner(
        self,
        *,
        finished_at: datetime,
        failure: str,
    ) -> tuple[OwnerActivation, ...]:
        """Terminalize unfinished Owner work and close any entered loop."""
        failed = self._context._activation.fail_interrupted(
            self.connection,
            self.state().triage_id,
            finished_at=finished_at,
            failure=failure,
        )
        for activation in failed:
            if activation.started_at is not None:
                self._stage_event(
                    self._context._activation.exited_event(
                        activation,
                        AgentExit(
                            status=AgentExitStatus.UNHANDLED_EXCEPTION,
                            content=failure,
                        ),
                        interrupted=True,
                    )
                )
            self._stage_event(self._context._activation.interrupted_event(activation))
        return failed

    def owner_message_id(self) -> str | None:
        """Read the live Owner checkpoint for a transactionally linked fact."""
        return self._context._owner().current_message_id(
            self.connection,
            self.state(),
        )

    def _commit(self) -> None:
        if self._state is not None:
            self._context._cached_state = self._state
        for event in self._events:
            self._context.event_bus.publish(event)
        self._events.clear()

    def _stage_event(self, event: ExecutionEvent) -> None:
        self._events.append(event)

    def _discard(self) -> None:
        self._state = None
        self._events.clear()


def _block_after_owner_failure(
    state: ProjectRuntimeState,
) -> ProjectRuntimeState:
    if state.status == "BLOCKED":
        return state
    if state.status == "DONE":
        return state
    return replace(state, status="BLOCKED")
