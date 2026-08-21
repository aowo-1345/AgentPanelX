"""Single ownership point for Feature identity, State, and write coordination."""

import fcntl
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock, get_ident
from typing import BinaryIO
from uuid import uuid4

from agentplanex.domains import (
    Action,
    AgentExit,
    AgentExitStatus,
    ExecutionEvent,
    OwnerActivation,
    ProjectOwnerTask,
    ProjectRuntimeState,
    RuntimeContextChangeReason,
    ToolExecutionResult,
)
from agentplanex.infrastructure.sqlite import SQLiteDatabase
from agentplanex.infrastructure.sqlite.repositories import (
    SQLiteProjectRuntimeStateRepository,
)
from agentplanex.services.event_bus import EventBus
from agentplanex.services.project_runtime_context._owner import _OwnerRuntime
from agentplanex.services.project_runtime_context._state import (
    StateMutation,
    apply_state_transition,
)
from agentplanex.services.project_runtime_error import FeatureBusyError


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
    _sealed: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        self.project_path = self.project_path.resolve()
        database_project_path = self.database.path.parent.parent.resolve()
        if database_project_path != self.project_path:
            raise ValueError("Runtime Context database does not belong to project path")

    def _bind_owner_runtime(
        self,
        owner_runtime: _OwnerRuntime,
    ) -> None:
        """Bind the private Owner runtime during composition only."""
        if self._sealed or self._owner_runtime is not None:
            raise RuntimeError("Project Runtime Context dependencies are already bound")
        self._owner_runtime = owner_runtime

    def _seal(self) -> None:
        if self._owner_runtime is None:
            raise RuntimeError("Project Runtime Context composition is incomplete")
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
                    raise RuntimeError(
                        "Project Owner identity exists without Runtime State"
                    )
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

    def run_owner_activation(self, activation: OwnerActivation) -> AgentExit:
        """Run one already-claimed model Activation against a fresh State snapshot."""
        try:
            with self.operation():
                return self._owner().run_activation(
                    self._reload_state(),
                    activation,
                )
        except Exception as error:
            return AgentExit(
                status=AgentExitStatus.UNHANDLED_EXCEPTION,
                content=f"{type(error).__name__}: {error}",
            )

    def execute_owner_activation_action(
        self,
        activation: OwnerActivation,
        action: Action,
    ) -> ToolExecutionResult:
        """Persist and execute one Tool step in an already-claimed Activation."""
        with self.operation():
            return self._owner().execute_activation_action(
                self._reload_state(),
                activation,
                action,
            )

    def reply_to_owner_activation(
        self,
        activation: OwnerActivation,
        content: str,
    ) -> AgentExit:
        """Persist a manual Owner reply before the Driver finalizes Activation."""
        with self.operation():
            return self._owner().reply_to_activation(
                self._reload_state(),
                activation,
                content,
            )

    def execute_tool(self, action: Action) -> ToolExecutionResult:
        """Execute a naked Tool action without changing Owner history."""
        with self.operation():
            return self._owner().execute_action(self._reload_state(), action)

    def append_owner_task_in_transaction(
        self,
        connection: sqlite3.Connection,
        state: ProjectRuntimeState,
        task: ProjectOwnerTask,
    ) -> tuple[str, str | None]:
        """Append external Owner input inside an existing business transaction."""
        self._require_operation()
        return self._owner().append_task(connection, state, task)

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

    def append_owner_task(
        self,
        task: ProjectOwnerTask,
    ) -> tuple[str, str | None]:
        """Append one external Owner input in this same atomic transaction."""
        return self._context._owner().append_task(
            self.connection,
            self.state(),
            task,
        )

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

    def _discard(self) -> None:
        self._state = None
        self._events.clear()
