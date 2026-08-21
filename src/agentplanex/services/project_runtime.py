"""Project-level command coordination over Owner, Planning, and Activations."""

import sqlite3
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from agentplanex.domains import (
    Action,
    ExecutionEvent,
    ExecutionEventType,
    OwnerActivation,
    ProjectOwnerTask,
    ProjectOwnerTaskType,
    ProjectRuntimeState,
    RuntimeContextChangeReason,
    StageRun,
    StageRunStatus,
    ToolExecutionResult,
)
from agentplanex.services.delivery import DeliveryService, MilestoneRunQueued
from agentplanex.services.delivery_runner import DeliveryDriveResult, DeliveryRunner
from agentplanex.services.event_bus import EventBus
from agentplanex.services.planning import PlanDecision, PlanningService
from agentplanex.services.project_runtime_context import (
    ActivationDriveResult,
    OwnerWorkState,
    ProjectRuntimeContext,
    ToolActivationDriveResult,
)


@dataclass(slots=True)
class ProjectRuntimeService:
    """Coordinate explicit project commands without hiding Owner activations."""

    planning: PlanningService
    delivery: DeliveryService
    delivery_runner: DeliveryRunner
    event_bus: EventBus
    context: ProjectRuntimeContext

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
            transaction.transition(
                reason=RuntimeContextChangeReason.CONVERSATION_STARTED,
                mutate=_start_conversation,
            )
            activation = transaction.submit_owner_input(task)
        return activation

    def approve_plan(self) -> PlanDecision:
        self._assert_plan_command_idle()
        return self.planning.approve_plan()

    def reject_plan(self, feedback: str = "") -> PlanDecision:
        self._assert_plan_command_idle()
        return self.planning.reject_plan(feedback)

    def drive_next_activation(self) -> ActivationDriveResult:
        """Claim and consume one activation for this project."""
        return self.context.drive_owner()

    def drive_until_waiting(self) -> ProjectRuntimeState:
        """Drive durable automatic work until control returns to a human."""
        return self._drive_until_waiting()

    def _drive_until_waiting(self) -> ProjectRuntimeState:
        while True:
            with self.context.transaction() as transaction:
                state = transaction.state()
            owner_work = self.context.owner_work()
            stage_run = self.delivery.active_stage_run()

            activation_runnable = owner_work is OwnerWorkState.RUNNABLE
            stage_runnable = stage_run is not None and stage_run.status is StageRunStatus.QUEUED
            if activation_runnable and stage_runnable:
                raise RuntimeError(
                    "Project Runtime invariant violated: Owner activation and "
                    "StageRun are both runnable"
                )
            if state.status in {"BLOCKED", "DONE"}:
                return state
            if state.pending_action is not None:
                return state
            if owner_work is not OwnerWorkState.IDLE:
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
            failed_activations = transaction.fail_interrupted_owner(
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
        for stage_run in failed_stages:
            self.event_bus.publish(_interrupted_stage_event(stage_run))
        return True

    def drive_activation_tool(self, action: Action) -> ToolActivationDriveResult:
        """Drive one activation step with a supplied Tool Action, without a model."""
        return self.context.drive_owner_tool(action)

    def fail_activation(self, reason: str) -> ToolActivationDriveResult:
        """Explicitly fail a waiting or interrupted Tool-driven Owner loop."""

        return self.context.fail_owner(reason)

    def reply_to_activation(self, content: str) -> ToolActivationDriveResult:
        """Finish a Tool-driven activation with a persisted Owner reply."""

        return self.context.reply_owner(content)

    def start_first_run(self) -> MilestoneRunQueued:
        """Apply the explicit first-Run command through the real Delivery Service."""
        with self.context.transaction() as transaction:
            state = transaction.state()
            if transaction.owner_work() is not OwnerWorkState.IDLE:
                raise ValueError("Project Owner already has unfinished work")
            self._assert_delivery_idle(transaction.connection, state.triage_id)
        return self.delivery.start_first_run()

    def drive_delivery(self) -> DeliveryDriveResult:
        """Run at most one durable Stage outside the Project Owner ReAct loop."""
        with self.context.transaction() as transaction:
            if transaction.owner_work() is not OwnerWorkState.IDLE:
                raise ValueError("Project Owner already has unfinished work")
        return self.delivery_runner.drive_once()

    def execute_action(self, action: Action) -> ToolExecutionResult:
        """Execute one explicit Tool Action without starting an Owner Loop."""

        return self.context.execute_tool(action)

    def _assert_plan_command_idle(self) -> None:
        with self.context.transaction() as transaction:
            state = transaction.state()
            self._assert_delivery_idle(transaction.connection, state.triage_id)
            if transaction.owner_work() is not OwnerWorkState.IDLE:
                raise ValueError("Project Owner already has unfinished work")

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
    return replace(context, status="TODO") if context.status == "TRIAGE" else context


def _begin_feature(context: ProjectRuntimeState) -> ProjectRuntimeState:
    if context.status != "TRIAGE":
        raise ValueError(
            f"Feature can only begin from TRIAGE: {context.triage_id} is {context.status}"
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
