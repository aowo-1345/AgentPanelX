"""Project-level command coordination over Context, Planning, and Delivery."""

from dataclasses import dataclass, replace
from datetime import UTC, datetime

from agentplanex.domains.execution_event import (
    RuntimeContextChangeReason,
)
from agentplanex.domains.project_runtime_state import ProjectRuntimeState
from agentplanex.services.delivery._service import DeliveryService
from agentplanex.services.delivery.contracts import (
    DeliveryDriveOutcome,
    DeliveryWorkState,
    MilestoneRunQueued,
)
from agentplanex.services.planning._service import PlanningService
from agentplanex.services.planning.contracts import PlanDecision
from agentplanex.services.project_runtime_context._activation import (
    OwnerWorkState,
)
from agentplanex.services.project_runtime_context.context import ProjectRuntimeContext
from agentplanex.services.project_runtime_context.models import (
    OwnerActivation,
    ProjectOwnerTask,
    ProjectOwnerTaskType,
)


@dataclass(slots=True)
class ProjectRuntimeService:
    """Own command operations and coordinate Context, Planning, and Delivery."""

    planning: PlanningService
    delivery: DeliveryService
    context: ProjectRuntimeContext

    def initialize(self) -> ProjectRuntimeState:
        """Create or restore the sole State and Owner without external input."""
        with self.context.operation():
            return self.context.initialize()

    def state(self) -> ProjectRuntimeState:
        """Restore the initialized Feature State without creating it."""
        return self.context.state()

    def begin_feature(self) -> ProjectRuntimeState:
        """Move one initialized Feature from TRIAGE to TODO without other work."""
        with self.context.operation():
            return self.context.transition(
                reason=RuntimeContextChangeReason.FEATURE_BEGUN,
                mutate=_begin_feature,
            )

    def submit_user_message(self, content: str) -> OwnerActivation:
        """Persist a user message and its durable Owner activation atomically."""
        with self.context.operation():
            task = ProjectOwnerTask(
                type=ProjectOwnerTaskType.USER_INPUT,
                content=content,
            )
            self._assert_delivery_idle()
            with self.context.transaction() as transaction:
                transaction.transition(
                    reason=RuntimeContextChangeReason.CONVERSATION_STARTED,
                    mutate=_start_conversation,
                )
                return transaction.submit_owner_input(task)

    def approve_plan(self) -> PlanDecision:
        with self.context.operation():
            self._assert_plan_command_idle()
            return self.planning.approve_plan()

    def reject_plan(self, feedback: str = "") -> PlanDecision:
        with self.context.operation():
            self._assert_plan_command_idle()
            return self.planning.reject_plan(feedback)

    def drive_until_waiting(self) -> ProjectRuntimeState:
        """Drive durable automatic work until control returns to a human."""
        with self.context.operation():
            return self._drive_until_waiting()

    def _drive_until_waiting(self) -> ProjectRuntimeState:
        while True:
            with self.context.transaction() as transaction:
                state = transaction.state()
            owner_work = self.context.owner_work()
            delivery_work = self.delivery.active_work()

            activation_runnable = owner_work is OwnerWorkState.RUNNABLE
            delivery_runnable = delivery_work is DeliveryWorkState.RUNNABLE
            if activation_runnable and delivery_runnable:
                raise RuntimeError(
                    "Project Runtime invariant violated: Owner activation and "
                    "StageRun are both runnable"
                )
            if state.status == "DONE":
                return state
            if state.pending_action is not None:
                return state
            if owner_work is not OwnerWorkState.IDLE:
                if not activation_runnable or delivery_work is not DeliveryWorkState.IDLE:
                    return state
                self.context.drive_owner()
                continue
            if state.status == "BLOCKED":
                return state
            if delivery_work is not DeliveryWorkState.IDLE:
                if not delivery_runnable:
                    return state
                self._drive_delivery_step()
                continue
            return state

    def fail_interrupted_work(self) -> bool:
        """Terminalize unfinished automatic work left by a stopped process."""
        with self.context.operation():
            return self._fail_interrupted_work()

    def _fail_interrupted_work(self) -> bool:
        finished_at = datetime.now(UTC)
        failure = "Project Runtime process was interrupted before work completed."
        owner_work = self.context.owner_work()
        delivery_work = self.delivery.active_work()
        if (
            owner_work is not OwnerWorkState.IDLE
            and delivery_work is not DeliveryWorkState.IDLE
        ):
            raise RuntimeError(
                "Project Runtime invariant violated: Owner and Delivery both have "
                "unfinished work"
            )
        if owner_work is not OwnerWorkState.IDLE:
            return self.context.reconcile_interrupted_owner(
                finished_at=finished_at,
                failure=failure,
            )
        if delivery_work is not DeliveryWorkState.IDLE:
            return self.delivery.reconcile_interrupted_work(
                finished_at=finished_at,
                failure=failure,
            )
        return False

    def start_first_run(self) -> MilestoneRunQueued:
        """Apply the explicit first-Run command through the real Delivery Service."""
        with self.context.operation():
            if self.context.owner_work() is not OwnerWorkState.IDLE:
                raise ValueError("Project Owner already has unfinished work")
            self._assert_delivery_idle()
            return self.delivery.start_first_run()

    def approve_blocked_run(self) -> MilestoneRunQueued:
        with self.context.operation():
            if self.context.owner_work() is not OwnerWorkState.IDLE:
                raise ValueError("Project Owner already has unfinished work")
            self._assert_delivery_idle()
            return self.delivery.approve_blocked_run()

    def reject_blocked_run(self, feedback: str) -> ProjectRuntimeState:
        with self.context.operation():
            if self.context.owner_work() is not OwnerWorkState.IDLE:
                raise ValueError("Project Owner already has unfinished work")
            self._assert_delivery_idle()
            return self.delivery.reject_blocked_run(feedback)

    def drive_delivery(self) -> DeliveryDriveOutcome:
        """Run at most one durable Stage outside the Project Owner ReAct loop."""
        with self.context.operation():
            return self._drive_delivery_step()

    def _drive_delivery_step(self) -> DeliveryDriveOutcome:
        if self.context.owner_work() is not OwnerWorkState.IDLE:
            raise ValueError("Project Owner already has unfinished work")
        return self.delivery.drive_next()

    def _assert_plan_command_idle(self) -> None:
        self._assert_delivery_idle()
        if self.context.owner_work() is not OwnerWorkState.IDLE:
            raise ValueError("Project Owner already has unfinished work")

    def _assert_delivery_idle(self) -> None:
        if self.delivery.active_work() is not DeliveryWorkState.IDLE:
            raise ValueError("Project delivery already has unfinished work")


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
