"""Project-scoped Runtime entry point and tool environment."""

from collections.abc import Callable
from pathlib import Path

from agentplanex.domains import (
    Action,
    OwnerActivation,
    ProjectRuntimeState,
    ToolExecutionResult,
)
from agentplanex.project_owner_agent.approval import ApprovalMode
from agentplanex.project_owner_agent.models.responses import (
    ResponsesTransport,
)
from agentplanex.project_runtime.composition import compose_project_runtime
from agentplanex.services import (
    ProjectControlView,
)
from agentplanex.services.delivery import MilestoneRunQueued
from agentplanex.services.delivery_runner import DeliveryDriveResult
from agentplanex.services.planning import PlanDecision
from agentplanex.services.project_runtime_context import (
    ActivationDriveResult,
    ToolActivationDriveResult,
)
from agentplanex.services.project_workspace import (
    ProjectWorkspaceView,
)
from agentplanex.services.stage_executor import StageExecutor
from agentplanex.settings import Settings


class ProjectRuntime:
    """Expose one persisted Project Owner through project-scoped commands."""

    def __init__(
        self,
        *,
        project_path: Path,
        settings: Settings,
        approval_mode: ApprovalMode,
        responses_transport: ResponsesTransport,
        stage_executor: StageExecutor | None = None,
    ) -> None:
        project_path = project_path.resolve()
        if not project_path.is_dir():
            raise ValueError(f"Project path is not a directory: {project_path}")
        components = compose_project_runtime(
            project_path=project_path,
            settings=settings,
            approval_mode=approval_mode,
            responses_transport=responses_transport,
            stage_executor=stage_executor,
        )
        self._service = components.service
        self._context = components.context
        self._workspace_query = components.workspace_query
        self._control_query = components.control_query
        self._git = components.git

    def initialize(self) -> ProjectRuntimeState:
        """Initialize this Feature Runtime without messages, activations, or models."""
        context = self._service.initialize()
        self._git.ensure_runtime_excluded()
        return context

    def state(self) -> ProjectRuntimeState:
        """Return this worktree's initialized Feature State without creating it."""
        return self._service.state()

    def begin_feature(self) -> ProjectRuntimeState:
        """Begin one selected Feature without creating an Owner activation."""
        return self._service.begin_feature()

    def submit_message(self, content: str) -> OwnerActivation:
        """Persist user input and enqueue one durable Owner activation."""
        return self._run(lambda: self._service.submit_user_message(content))

    def approve_plan(self) -> PlanDecision:
        """Approve the pending Plan and enqueue the Owner decision input."""
        return self._run(self._service.approve_plan)

    def reject_plan(self, feedback: str = "") -> PlanDecision:
        """Reject the pending Plan and enqueue the Owner decision input."""
        return self._run(lambda: self._service.reject_plan(feedback))

    def drive_next_activation(self) -> ActivationDriveResult:
        """Claim and process one pending Owner activation."""
        return self._run(self._service.drive_next_activation)

    def drive_until_waiting(self) -> ProjectRuntimeState:
        """Run durable automatic work until control must return to the user."""
        return self._run(self._service.drive_until_waiting)

    def fail_interrupted_work(self) -> bool:
        """Fail unfinished automatic work left by a stopped Runtime process."""
        return self._run(self._service.fail_interrupted_work)

    def drive_activation_tool(self, action: Action) -> ToolActivationDriveResult:
        """Drive one Owner activation step with a supplied Tool Action."""

        return self._run(lambda: self._service.drive_activation_tool(action))

    def reply_to_activation(self, content: str) -> ToolActivationDriveResult:
        """Finish a Tool-driven Owner activation with a persisted reply."""

        return self._run(lambda: self._service.reply_to_activation(content))

    def fail_activation(self, reason: str) -> ToolActivationDriveResult:
        """Explicitly fail a Tool-driven Owner activation."""

        return self._run(lambda: self._service.fail_activation(reason))

    def start_first_run(self) -> MilestoneRunQueued:
        """Apply the first explicit Run approval and queue its first Stage."""
        return self._run(self._service.start_first_run)

    def drive_delivery(self) -> DeliveryDriveResult:
        """Run one queued Stage through the Delivery Driver."""
        return self._run(self._service.drive_delivery)

    def project_control_view(self) -> ProjectControlView:
        """Return the stable read model used by control clients and debug tooling."""
        return self._control_query.get_current()

    def project_workspace_view(self, triage_id: str) -> ProjectWorkspaceView:
        """Return independently degradable panels for one Web workspace."""
        return self._workspace_query.get(triage_id)

    def execute_action(self, action: Action) -> ToolExecutionResult:
        """Execute one explicit tool action without entering the Agent loop."""
        return self._run(lambda: self._service.execute_action(action))

    def _run[T](self, command: Callable[[], T]) -> T:
        with self._context.operation():
            return command()
