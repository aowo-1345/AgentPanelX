"""Project-level command coordination over Owner, Planning, and Activations."""

import json
import sqlite3
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from agentplanex.domains import (
    Action,
    AgentExit,
    AgentExitStatus,
    ExecutionEvent,
    ExecutionEventType,
    OwnerActivation,
    OwnerActivationMode,
    OwnerActivationStatus,
    ProjectOwnerTask,
    ProjectOwnerTaskType,
    ProjectRuntimeContext,
    RuntimeContextChangeReason,
    ToolExecutionResult,
)
from agentplanex.infrastructure.sqlite import SQLiteDatabase
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
from agentplanex.services.project_owner import ProjectOwnerService
from agentplanex.services.runtime_context import RuntimeContextService

type PlanDecisionAction = Literal["approve", "reject"]


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

    database: SQLiteDatabase
    owner: ProjectOwnerService
    planning: PlanningService
    delivery: DeliveryService
    delivery_runner: DeliveryRunner
    controls: ProjectControlQuery
    event_bus: EventBus
    runtime_contexts: RuntimeContextService
    activations: SQLiteOwnerActivationRepository
    driver: OwnerActivationDriver

    def initialize(self) -> ProjectRuntimeContext:
        """Create or restore the sole Context and Owner without external input."""
        with self.database.transaction() as connection:
            return self.owner.ensure_state(connection)

    def begin_feature(self, triage_id: str) -> ProjectRuntimeContext:
        """Move one initialized Feature from TRIAGE to TODO without other work."""
        return self.runtime_contexts.transition(
            triage_id,
            reason=RuntimeContextChangeReason.FEATURE_BEGUN,
            mutate=_begin_feature,
        )

    def submit_user_message(self, content: str) -> OwnerActivation:
        """Persist a user message and its durable Owner activation atomically."""
        task = ProjectOwnerTask(
            type=ProjectOwnerTaskType.USER_INPUT,
            content=content,
        )
        with self.database.transaction() as connection:
            context = self.owner.ensure_state(connection)
            self._assert_delivery_idle(connection, context.triage_id)
            self._assert_owner_idle(connection, context.triage_id)
            message_id, summary_id = self.owner.append_task(
                connection,
                context,
                task,
            )
            updated, context_event = self.runtime_contexts.transition_in_transaction(
                connection,
                context.triage_id,
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
            self.activations.insert(connection, activation)

        if context_event is not None:
            self.event_bus.publish(context_event)
        return activation

    def approve_plan(self) -> PlanDecision:
        return self._submit_plan_decision("approve", "")

    def reject_plan(self, feedback: str = "") -> PlanDecision:
        return self._submit_plan_decision("reject", feedback)

    def drive_next_activation(self) -> ActivationDriveResult:
        """Claim and consume one activation for this project."""
        with self.database.transaction() as connection:
            context = self.owner.ensure_state(connection)
        return self.driver.drive_next(context.triage_id)

    def fail_interrupted_model_activation(self) -> OwnerActivation | None:
        """Fail a model-owned activation left RUNNING across process restart."""
        with self.database.transaction() as connection:
            context = self.owner.ensure_state(connection)
            activation = self.activations.get_unfinished(
                connection, context.triage_id
            )
            if (
                activation is None
                or activation.status is not OwnerActivationStatus.RUNNING
                or activation.driver_mode is not OwnerActivationMode.MODEL
            ):
                return None
            failed = self.activations.mark_failed(
                connection,
                activation.activation_id,
                datetime.now(UTC),
                "AgentPlaneX Web stopped while this activation was running.",
            )
        if failed.driver_mode is None:
            raise RuntimeError("Recovered activation has no driver mode")
        self.event_bus.publish(
            ExecutionEvent(
                triage_id=failed.triage_id,
                event_type=ExecutionEventType.REACT_LOOP_EXITED,
                react_loop_id=failed.activation_id,
                payload={
                    "agent_exit_status": AgentExitStatus.UNHANDLED_EXCEPTION.value,
                    "driver_mode": failed.driver_mode.value,
                    "recovered_after_restart": True,
                },
            )
        )
        return failed

    def drive_activation_tool(self, action: Action) -> ToolActivationDriveResult:
        """Drive one activation step with a supplied Tool Action, without a model."""

        with self.database.transaction() as connection:
            context = self.owner.ensure_state(connection)
        claim = self.driver.claim_for_tool(context.triage_id)
        if claim.started:
            self._publish_tool_loop_entered(claim.activation)

        try:
            tool_result = self.owner.execute_activation_action(
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
            self._publish_tool_loop_exited(activation, result_exit)
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
        with self.database.transaction() as connection:
            context = self.owner.ensure_state(connection)
        claim = self.driver.claim_for_tool_failure(context.triage_id)
        if claim.started:
            self._publish_tool_loop_entered(claim.activation)
        result_exit = AgentExit(
            status=AgentExitStatus.MANUAL_DRIVE_FAILED,
            content=failure,
        )
        activation = self.driver.finish(claim.activation, result_exit)
        self._publish_tool_loop_exited(activation, result_exit)
        return ToolActivationDriveResult(
            activation=activation,
            started=claim.started,
            tool_result=None,
            exit=result_exit,
        )

    def reply_to_activation(self, content: str) -> ToolActivationDriveResult:
        """Finish a Tool-driven activation with a persisted Owner reply."""

        with self.database.transaction() as connection:
            context = self.owner.ensure_state(connection)
        claim = self.driver.claim_for_tool(context.triage_id)
        if claim.started:
            self._publish_tool_loop_entered(claim.activation)
        try:
            result_exit = self.owner.reply_to_activation(
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
        self._publish_tool_loop_exited(activation, result_exit)
        return ToolActivationDriveResult(
            activation=activation,
            started=claim.started,
            tool_result=None,
            exit=result_exit,
        )

    def start_first_run(self) -> MilestoneRunQueued:
        """Apply the explicit first-Run command through the real Delivery Service."""
        with self.database.transaction() as connection:
            context = self.owner.ensure_state(connection)
            self._assert_owner_idle(connection, context.triage_id)
            self._assert_delivery_idle(connection, context.triage_id)
        return self.delivery.start_first_run(context)

    def drive_delivery(self) -> DeliveryDriveResult:
        """Run at most one durable Stage outside the Project Owner ReAct loop."""
        with self.database.transaction() as connection:
            context = self.owner.ensure_state(connection)
            self._assert_owner_idle(connection, context.triage_id)
        return self.delivery_runner.drive_once(
            context.triage_id,
            append_execution_result=self._append_execution_result,
        )

    def project_control_view(self) -> ProjectControlView:
        """Return the one composed, read-only control projection for this project."""
        with self.database.transaction() as connection:
            context = self.owner.ensure_state(connection)
        return self.controls.get(context.triage_id)

    def execute_action(self, action: Action) -> ToolExecutionResult:
        """Execute one explicit Tool Action without starting an Owner Loop."""

        with self.database.transaction() as connection:
            context = self.owner.ensure_state(connection)
            unfinished = self.activations.get_unfinished(
                connection,
                context.triage_id,
            )
        if unfinished is not None:
            raise ValueError(
                "Project Owner has an unfinished activation; use drive tool so "
                f"the Action is bound to {unfinished.activation_id}"
            )
        return self.owner.execute_action(action)

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
        self._publish_tool_loop_exited(failed, result_exit)
        return ToolActivationDriveResult(
            activation=failed,
            started=started,
            tool_result=None,
            exit=result_exit,
        )

    def _publish_tool_loop_entered(self, activation: OwnerActivation) -> None:
        self.event_bus.publish(
            ExecutionEvent(
                triage_id=activation.triage_id,
                event_type=ExecutionEventType.REACT_LOOP_ENTERED,
                react_loop_id=activation.activation_id,
                payload={
                    "task_type": activation.task_type.value,
                    "driver_mode": "TOOL",
                },
            )
        )

    def _publish_tool_loop_exited(
        self,
        activation: OwnerActivation,
        result: AgentExit,
    ) -> None:
        self.event_bus.publish(
            ExecutionEvent(
                triage_id=activation.triage_id,
                event_type=ExecutionEventType.REACT_LOOP_EXITED,
                react_loop_id=activation.activation_id,
                payload={
                    "agent_exit_status": result.status.value,
                    "driver_mode": "TOOL",
                },
            )
        )

    def _submit_plan_decision(
        self,
        action: PlanDecisionAction,
        feedback: str,
    ) -> PlanDecision:
        with self.database.transaction() as connection:
            context = self.owner.ensure_state(connection)
            self._assert_delivery_idle(connection, context.triage_id)
            self._assert_owner_idle(connection, context.triage_id)
        task = ProjectOwnerTask(
            type=ProjectOwnerTaskType.PLAN_DECISION,
            content=_plan_decision_message(
                action,
                feedback,
                context.pending_plan_subject_digest,
            ),
        )

        def append_message(
            connection: sqlite3.Connection,
        ) -> tuple[str, str | None]:
            current = self.owner.ensure_state(connection)
            if current.triage_id != context.triage_id:
                raise RuntimeError("Project Runtime Context changed during command")
            self._assert_delivery_idle(connection, current.triage_id)
            self._assert_owner_idle(connection, current.triage_id)
            return self.owner.append_task(connection, current, task)

        if action == "approve":
            return self.planning.approve_plan(
                context.triage_id,
                append_message=append_message,
            )
        return self.planning.reject_plan(
            context.triage_id,
            append_message=append_message,
        )

    def _append_execution_result(
        self,
        connection: sqlite3.Connection,
        context: ProjectRuntimeContext,
        content: str,
    ) -> OwnerActivation:
        owner_context = self.owner.ensure_state(connection)
        if owner_context.triage_id != context.triage_id:
            raise RuntimeError("Project Runtime Context changed during Stage completion")
        self._assert_owner_idle(connection, context.triage_id)
        task = ProjectOwnerTask(
            type=ProjectOwnerTaskType.EXECUTION_RESULT,
            content=content,
        )
        message_id, summary_id = self.owner.append_task(
            connection,
            owner_context,
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


def _start_conversation(context: ProjectRuntimeContext) -> ProjectRuntimeContext:
    return (
        replace(context, status="TODO")
        if context.status == "TRIAGE"
        else context
    )


def _begin_feature(context: ProjectRuntimeContext) -> ProjectRuntimeContext:
    if context.status != "TRIAGE":
        raise ValueError(
            "Feature can only begin from TRIAGE: "
            f"{context.triage_id} is {context.status}"
        )
    return replace(context, status="TODO")


def _plan_decision_message(
    action: PlanDecisionAction,
    feedback: str,
    subject_digest: str | None,
) -> str:
    approved = action == "approve"
    return json.dumps(
        {
            "event": "PLAN_DECISION_RECEIVED",
            "decision": "APPROVED" if approved else "REJECTED",
            "plan_subject_digest": subject_digest,
            "feedback": feedback.strip() or None,
            "required_response": (
                "Reconcile the complete Milestone View with the approved Plan, then "
                "request the first or next unfinished Milestone when delivery is ready."
                if approved
                else "Revise the canonical Specs with the user, then request approval "
                "again only when the complete Plan is ready."
            ),
        },
        ensure_ascii=False,
        indent=2,
    )
