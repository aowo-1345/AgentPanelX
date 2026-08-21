"""Project-level command coordination over Context, Planning, and Delivery."""

from dataclasses import dataclass, replace
from datetime import UTC, datetime

from agentplanex.domains import (
    Action,
    OwnerActivation,
    ProjectOwnerTask,
    ProjectOwnerTaskType,
    ProjectRuntimeState,
    RuntimeContextChangeReason,
    ToolExecutionResult,
)
from agentplanex.services.delivery import (
    DeliveryDriveOutcome,
    DeliveryService,
    DeliveryWorkState,
    MilestoneRunQueued,
)
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
        self._assert_delivery_idle()
        with self.context.transaction() as transaction:
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
            delivery_work = self.delivery.active_work()

            activation_runnable = owner_work is OwnerWorkState.RUNNABLE
            delivery_runnable = delivery_work is DeliveryWorkState.RUNNABLE
            if activation_runnable and delivery_runnable:
                raise RuntimeError(
                    "Project Runtime invariant violated: Owner activation and "
                    "StageRun are both runnable"
                )
            if state.status in {"BLOCKED", "DONE"}:
                return state
            if state.pending_action is not None:
                return state
            if owner_work is not OwnerWorkState.IDLE:
                if not activation_runnable or delivery_work is not DeliveryWorkState.IDLE:
                    return state
                self.drive_next_activation()
                continue
            if delivery_work is not DeliveryWorkState.IDLE:
                if not delivery_runnable:
                    return state
                self.drive_delivery()
                continue
            return state

    def fail_interrupted_work(self) -> bool:
        """Terminalize unfinished automatic work left by a stopped process."""
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
        if self.context.owner_work() is not OwnerWorkState.IDLE:
            raise ValueError("Project Owner already has unfinished work")
        self._assert_delivery_idle()
        return self.delivery.start_first_run()

    def drive_delivery(self) -> DeliveryDriveOutcome:
        """Run at most one durable Stage outside the Project Owner ReAct loop."""
        if self.context.owner_work() is not OwnerWorkState.IDLE:
            raise ValueError("Project Owner already has unfinished work")
        return self.delivery.drive_next()

    def execute_action(self, action: Action) -> ToolExecutionResult:
        """Execute one explicit Tool Action without starting an Owner Loop."""

        return self.context.execute_tool(action)

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
