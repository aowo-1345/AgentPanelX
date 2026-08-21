"""Project-level command coordination over Owner, Planning, and Activations."""

import sqlite3
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import uuid4

from agentplanex.domains import (
    Action,
    AgentExit,
    AgentExitStatus,
    ExecutionEvent,
    ExecutionEventType,
    OwnerActivation,
    OwnerActivationStatus,
    ProjectOwnerTask,
    ProjectOwnerTaskType,
    ProjectRuntimeState,
    RuntimeContextChangeReason,
    StageRun,
    StageRunStatus,
    ToolExecutionResult,
)
from agentplanex.infrastructure.sqlite.repositories import (
    SQLiteOwnerActivationRepository,
)
from agentplanex.services.delivery import DeliveryService, MilestoneRunQueued
from agentplanex.services.delivery_runner import DeliveryDriveResult, DeliveryRunner
from agentplanex.services.event_bus import EventBus
from agentplanex.services.owner_activation import (
    ActivationDriveResult,
    OwnerActivationDriver,
)
from agentplanex.services.planning import PlanDecision, PlanningService
from agentplanex.services.project_control import ProjectControlQuery, ProjectControlView
from agentplanex.services.project_runtime_context import ProjectRuntimeContext


@dataclass(frozen=True, slots=True)
class ToolActivationDriveResult:
    """One developer-supplied step inside a durable Owner activation."""

    activation: OwnerActivation
    started: bool
    tool_result: ToolExecutionResult | None
    exit: AgentExit | None


@dataclass(slots=True)
class ProjectRuntimeService:
    """Coordinate explicit project commands without hiding Owner activations."""

    planning: PlanningService
    delivery: DeliveryService
    delivery_runner: DeliveryRunner
    controls: ProjectControlQuery
    event_bus: EventBus
    context: ProjectRuntimeContext
    activations: SQLiteOwnerActivationRepository
    driver: OwnerActivationDriver

    def initialize(self) -> ProjectRuntimeState:
        """Create or restore the sole State and Owner without external input."""
        return self.context.initialize()

    def state(self) -> ProjectRuntimeState:
        """Restore the initialized Feature State without creating it."""
        return self.context.state()

    def begin_feature(self) -> ProjectRuntimeState:
        """Move one initialized Feature from TRIAGE to TODO without other work."""
        return self.context.transition(
            reason=RuntimeContextChangeReason.FEATURE_BEGUN,
            mutate=_begin_feature,
        )

    def submit_user_message(self, content: str) -> OwnerActivation:
        """Persist a user message and its durable Owner activation atomically."""
        task = ProjectOwnerTask(
            type=ProjectOwnerTaskType.USER_INPUT,
            content=content,
        )
        with self.context.transaction() as transaction:
            state = transaction.state()
            self._assert_delivery_idle(transaction.connection, state.triage_id)
            self._assert_owner_idle(transaction.connection, state.triage_id)
            message_id, summary_id = transaction.append_owner_task(task)
            updated = transaction.transition(
                reason=RuntimeContextChangeReason.CONVERSATION_STARTED,
                mutate=_start_conversation,
            )
            activation = OwnerActivation(
                activation_id=uuid4().hex,
                triage_id=updated.triage_id,
                task_type=task.type,
                message_id=message_id,
                summary_id=summary_id,
            )
            self.activations.insert(transaction.connection, activation)
        return activation

    def approve_plan(self) -> PlanDecision:
        self._assert_plan_command_idle()
        return self.planning.approve_plan()

    def reject_plan(self, feedback: str = "") -> PlanDecision:
        self._assert_plan_command_idle()
        return self.planning.reject_plan(feedback)

    def drive_next_activation(self) -> ActivationDriveResult:
        """Claim and consume one activation for this project."""
        result = self.driver.drive_next(self.context.state().triage_id)
        if (
            result.activation is not None
            and result.activation.status is OwnerActivationStatus.FAILED
        ):
            self._block_failed_activation(result.activation)
        return result

    def drive_until_waiting(self) -> ProjectRuntimeState:
        """Drive durable automatic work until control returns to a human."""
        return self._drive_until_waiting()

    def _drive_until_waiting(self) -> ProjectRuntimeState:
        while True:
            with self.context.transaction() as transaction:
                state = transaction.state()
            activation = self.driver.unfinished(state.triage_id)
            stage_run = self.delivery.active_stage_run(state.triage_id)

            activation_runnable = (
                activation is not None
                and activation.status is OwnerActivationStatus.PENDING
                and activation.driver_mode is None
            )
            stage_runnable = (
                stage_run is not None
                and stage_run.status is StageRunStatus.QUEUED
            )
            if activation_runnable and stage_runnable:
                raise RuntimeError(
                    "Project Runtime invariant violated: Owner activation and "
                    "StageRun are both runnable"
                )
            if state.status in {"BLOCKED", "DONE"}:
                return state
            if state.pending_action is not None:
                return state
            if activation is not None:
                if not activation_runnable or stage_run is not None:
                    return state
                self.drive_next_activation()
                continue
            if stage_run is not None:
                if not stage_runnable:
                    return state
                self.drive_delivery()
                continue
            return state

    def fail_interrupted_work(self) -> bool:
        """Terminalize unfinished automatic work left by a stopped process."""
        finished_at = datetime.now(UTC)
        failure = "Project Runtime process was interrupted before work completed."
        with self.context.transaction() as transaction:
            state = transaction.state()
            failed_activations = self.driver.fail_interrupted(
                transaction.connection,
                state.triage_id,
                finished_at=finished_at,
                failure=failure,
            )
            failed_stages = self.delivery.fail_interrupted_stage_runs(
                transaction.connection,
                state.triage_id,
                finished_at=finished_at,
                failure=failure,
            )
            if not failed_activations and not failed_stages:
                return False
            transaction.transition(
                reason=RuntimeContextChangeReason.INTERRUPTED_WORK_FAILED,
                mutate=_block_runtime_execution,
            )
        for activation in failed_activations:
            self.event_bus.publish(_interrupted_activation_event(activation))
        for stage_run in failed_stages:
            self.event_bus.publish(_interrupted_stage_event(stage_run))
        return True

    def drive_activation_tool(self, action: Action) -> ToolActivationDriveResult:
        """Drive one activation step with a supplied Tool Action, without a model."""

        claim = self.driver.claim_for_tool(self.context.state().triage_id)

        try:
            tool_result = self.context.execute_owner_activation_action(
                claim.activation,
                action,
            )
        except Exception as error:
            return self._fail_tool_activation(
                claim.activation,
                claim.started,
                error,
            )

        result_exit = tool_result.exit
        activation = claim.activation
        if result_exit is not None:
            activation = self.driver.finish(activation, result_exit)
            if activation.status is OwnerActivationStatus.FAILED:
                self._block_failed_activation(activation)
        else:
            activation = self.driver.release_tool(activation)
        return ToolActivationDriveResult(
            activation=activation,
            started=claim.started,
            tool_result=tool_result,
            exit=result_exit,
        )

    def fail_activation(self, reason: str) -> ToolActivationDriveResult:
        """Explicitly fail a waiting or interrupted Tool-driven Owner loop."""

        failure = reason.strip()
        if not failure:
            raise ValueError("Project Owner failure reason must not be empty")
        claim = self.driver.claim_for_tool_failure(self.context.state().triage_id)
        result_exit = AgentExit(
            status=AgentExitStatus.MANUAL_DRIVE_FAILED,
            content=failure,
        )
        activation = self.driver.finish(claim.activation, result_exit)
        self._block_failed_activation(activation)
        return ToolActivationDriveResult(
            activation=activation,
            started=claim.started,
            tool_result=None,
            exit=result_exit,
        )

    def reply_to_activation(self, content: str) -> ToolActivationDriveResult:
        """Finish a Tool-driven activation with a persisted Owner reply."""

        claim = self.driver.claim_for_tool(self.context.state().triage_id)
        try:
            result_exit = self.context.reply_to_owner_activation(
                claim.activation,
                content,
            )
        except Exception as error:
            return self._fail_tool_activation(
                claim.activation,
                claim.started,
                error,
            )

        activation = self.driver.finish(claim.activation, result_exit)
        return ToolActivationDriveResult(
            activation=activation,
            started=claim.started,
            tool_result=None,
            exit=result_exit,
        )

    def start_first_run(self) -> MilestoneRunQueued:
        """Apply the explicit first-Run command through the real Delivery Service."""
        with self.context.transaction() as transaction:
            state = transaction.state()
            self._assert_owner_idle(transaction.connection, state.triage_id)
            self._assert_delivery_idle(transaction.connection, state.triage_id)
        return self.delivery.start_first_run(state)

    def drive_delivery(self) -> DeliveryDriveResult:
        """Run at most one durable Stage outside the Project Owner ReAct loop."""
        with self.context.transaction() as transaction:
            state = transaction.state()
            self._assert_owner_idle(transaction.connection, state.triage_id)
        return self.delivery_runner.drive_once(
            state.triage_id,
            append_execution_result=self._append_execution_result,
        )

    def project_control_view(self) -> ProjectControlView:
        """Return the one composed, read-only control projection for this project."""
        return self.controls.get(self.context.state().triage_id)

    def execute_action(self, action: Action) -> ToolExecutionResult:
        """Execute one explicit Tool Action without starting an Owner Loop."""

        with self.context.transaction() as transaction:
            state = transaction.state()
            unfinished = self.activations.get_unfinished(
                transaction.connection,
                state.triage_id,
            )
        if unfinished is not None:
            raise ValueError(
                "Project Owner has an unfinished activation; use drive tool so "
                f"the Action is bound to {unfinished.activation_id}"
            )
        return self.context.execute_tool(action)

    def _fail_tool_activation(
        self,
        activation: OwnerActivation,
        started: bool,
        error: Exception,
    ) -> ToolActivationDriveResult:
        result_exit = AgentExit(
            status=AgentExitStatus.UNHANDLED_EXCEPTION,
            content=f"{type(error).__name__}: {error}",
        )
        failed = self.driver.finish(activation, result_exit)
        self._block_failed_activation(failed)
        return ToolActivationDriveResult(
            activation=failed,
            started=started,
            tool_result=None,
            exit=result_exit,
        )

    def _block_failed_activation(self, activation: OwnerActivation) -> None:
        if activation.status is not OwnerActivationStatus.FAILED:
            raise ValueError("Only a failed Owner activation can block the Runtime")
        state = self.context.state()
        if state.triage_id != activation.triage_id:
            raise ValueError("Owner activation does not belong to this Runtime")
        self.context.transition(
            reason=RuntimeContextChangeReason.OWNER_ACTIVATION_FAILED,
            mutate=_block_runtime_execution,
        )

    def _assert_plan_command_idle(self) -> None:
        with self.context.transaction() as transaction:
            state = transaction.state()
            self._assert_delivery_idle(transaction.connection, state.triage_id)
            self._assert_owner_idle(transaction.connection, state.triage_id)

    def _append_execution_result(
        self,
        connection: sqlite3.Connection,
        context: ProjectRuntimeState,
        content: str,
    ) -> OwnerActivation:
        current = self.context.state()
        if current.triage_id != context.triage_id:
            raise RuntimeError("Project Runtime Context changed during Stage completion")
        self._assert_owner_idle(connection, context.triage_id)
        task = ProjectOwnerTask(
            type=ProjectOwnerTaskType.EXECUTION_RESULT,
            content=content,
        )
        message_id, summary_id = self.context.append_owner_task_in_transaction(
            connection,
            current,
            task,
        )
        activation = OwnerActivation(
            activation_id=uuid4().hex,
            triage_id=context.triage_id,
            task_type=task.type,
            message_id=message_id,
            summary_id=summary_id,
        )
        self.activations.insert(connection, activation)
        return activation

    def _assert_owner_idle(
        self,
        connection: sqlite3.Connection,
        triage_id: str,
    ) -> None:
        unfinished = self.activations.get_unfinished(connection, triage_id)
        if unfinished is not None:
            raise ValueError(
                "Project Owner already has an unfinished activation: "
                f"{unfinished.activation_id} ({unfinished.status.value})"
            )

    def _assert_delivery_idle(
        self,
        connection: sqlite3.Connection,
        triage_id: str,
    ) -> None:
        active = self.delivery.stage_runs.get_active(connection, triage_id)
        if active is not None:
            raise ValueError(
                "Project delivery already has an active StageRun: "
                f"{active.stage_run_id} ({active.status.value})"
            )


def _start_conversation(context: ProjectRuntimeState) -> ProjectRuntimeState:
    if context.blocked_reason is not None:
        if context.blocked_previous_status is None:
            raise ValueError("User-intervention blocker has no previous status")
        return replace(
            context,
            status=context.blocked_previous_status,
            blocked_reason=None,
            blocked_capability=None,
            blocked_previous_status=None,
        )
    return (
        replace(context, status="TODO")
        if context.status == "TRIAGE"
        else context
    )


def _begin_feature(context: ProjectRuntimeState) -> ProjectRuntimeState:
    if context.status != "TRIAGE":
        raise ValueError(
            "Feature can only begin from TRIAGE: "
            f"{context.triage_id} is {context.status}"
        )
    return replace(context, status="TODO")


def _block_runtime_execution(
    context: ProjectRuntimeState,
) -> ProjectRuntimeState:
    if context.status == "BLOCKED":
        return context
    if context.status == "DONE":
        raise ValueError("Completed Project Runtime cannot contain failed work")
    return replace(context, status="BLOCKED")


def _interrupted_activation_event(activation: OwnerActivation) -> ExecutionEvent:
    if activation.status is not OwnerActivationStatus.FAILED:
        raise ValueError("Interrupted Activation event requires a failed Activation")
    if activation.driver_mode is None or activation.failure is None:
        raise ValueError("Failed Activation is missing its terminal facts")
    return ExecutionEvent(
        triage_id=activation.triage_id,
        event_type=ExecutionEventType.OWNER_ACTIVATION_FAILED,
        react_loop_id=(
            activation.activation_id if activation.started_at is not None else None
        ),
        payload={
            "activation_id": activation.activation_id,
            "task_type": activation.task_type.value,
            "driver_mode": activation.driver_mode.value,
            "failure": activation.failure,
            "interrupted": True,
            "started": activation.started_at is not None,
        },
    )

def _interrupted_stage_event(stage_run: StageRun) -> ExecutionEvent:
    if stage_run.status is not StageRunStatus.FAILED or stage_run.failure is None:
        raise ValueError("Interrupted Stage event requires a failed StageRun")
    return ExecutionEvent(
        triage_id=stage_run.triage_id,
        event_type=ExecutionEventType.STAGE_RUN_FAILED,
        payload={
            "stage_run_id": stage_run.stage_run_id,
            "run_id": stage_run.run_id,
            "snapshot_id": stage_run.snapshot_id,
            "milestone_key": stage_run.milestone_key,
            "stage_key": stage_run.stage_key,
            "input_commit_sha": stage_run.input_commit_sha,
            "failure": stage_run.failure,
            "interrupted": True,
            "started": stage_run.started_at is not None,
        },
    )
