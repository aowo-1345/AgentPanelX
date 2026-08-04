"""Project-level command coordination over Owner, Planning, and Activations."""

import sqlite3
from dataclasses import dataclass, replace
from typing import Literal
from uuid import uuid4

from agentplanex.domains import (
    Action,
    OwnerActivation,
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
            message_id = self.owner.append_task(connection, context, task)
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
        return self.owner.execute_action(action)

    def _submit_plan_decision(
        self,
        action: PlanDecisionAction,
        feedback: str,
    ) -> PlanDecision:
        content = (
            "The user approved the current Plan."
            if action == "approve"
            else _plan_rejection_message(feedback)
        )
        task = ProjectOwnerTask(
            type=ProjectOwnerTaskType.PLAN_DECISION,
            content=content,
        )
        with self.database.transaction() as connection:
            context = self.owner.ensure_state(connection)
            self._assert_delivery_idle(connection, context.triage_id)
            self._assert_owner_idle(connection, context.triage_id)

        def append_message(connection: sqlite3.Connection) -> str:
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
        message_id = self.owner.append_task(connection, owner_context, task)
        activation = OwnerActivation(
            activation_id=uuid4().hex,
            triage_id=context.triage_id,
            task_type=task.type,
            message_id=message_id,
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


def _plan_rejection_message(feedback: str) -> str:
    message = "The user rejected the current Plan."
    return f"{message} Feedback: {feedback.strip()}" if feedback.strip() else message
