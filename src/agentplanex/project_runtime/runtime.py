"""Project-scoped Runtime entry point and tool environment."""

from pathlib import Path

from agentplanex.domains import (
    Action,
    OwnerActivation,
    ToolExecutionResult,
)
from agentplanex.infrastructure.git_repository import GitRepository
from agentplanex.infrastructure.sqlite import SQLiteDatabase, initialize_schema
from agentplanex.infrastructure.sqlite.repositories import (
    SQLiteOwnerActivationRepository,
)
from agentplanex.infrastructure.sqlite.timeline import SQLiteTimelineRecorder
from agentplanex.project_owner_agent.approval import ApprovalMode
from agentplanex.project_runtime.executions import create_project_executions
from agentplanex.services import (
    AgentCollaborationService,
    DeliveryService,
    EventBus,
    PlanningService,
    ProjectControlQuery,
    ProjectControlView,
    ProjectOwnerService,
    ProjectRuntimeService,
    RuntimeContextService,
)
from agentplanex.services.delivery import MilestoneRunQueued
from agentplanex.services.delivery_runner import DeliveryDriveResult, DeliveryRunner
from agentplanex.services.owner_activation import (
    ActivationDriveResult,
    OwnerActivationDriver,
)
from agentplanex.services.plan_hard_gate import CodexPlanHardGate
from agentplanex.services.planning import PlanDecision
from agentplanex.services.stage_executor import CodexStageExecutor
from agentplanex.settings import Settings


class ProjectRuntime:
    """Expose one persisted Project Owner through project-scoped commands."""

    def __init__(
        self,
        *,
        project_path: Path,
        settings: Settings,
        approval_mode: ApprovalMode,
    ) -> None:
        project_path = project_path.resolve()
        if not project_path.is_dir():
            raise ValueError(f"Project path is not a directory: {project_path}")

        database = SQLiteDatabase.for_project(project_path)
        initialize_schema(database)
        event_bus = EventBus((SQLiteTimelineRecorder(database),))
        runtime_contexts = RuntimeContextService(database, event_bus)
        activations = SQLiteOwnerActivationRepository()
        collaboration = AgentCollaborationService.from_settings(
            project_path,
            settings.runtime,
        )
        hard_gate = CodexPlanHardGate(collaboration)
        planning = PlanningService(
            project_path=project_path,
            database=database,
            event_bus=event_bus,
            runtime_contexts=runtime_contexts,
            activations=activations,
            review_plan=hard_gate.review,
        )
        delivery = DeliveryService(
            project_path=project_path,
            database=database,
            event_bus=event_bus,
            runtime_contexts=runtime_contexts,
            git=GitRepository(project_path),
            review_milestones=hard_gate.review_milestones,
        )
        delivery_runner = DeliveryRunner(
            delivery=delivery,
            executor=CodexStageExecutor(collaboration.transport),
            git=GitRepository(project_path),
        )
        controls = ProjectControlQuery(
            database=database,
            git=GitRepository(project_path),
        )
        executions = create_project_executions(
            project_path,
            settings.runtime,
            planning,
            delivery,
            collaboration,
            event_bus,
        )
        owner = ProjectOwnerService(
            database=database,
            settings=settings,
            approval_mode=approval_mode,
            tools=executions.tools,
            tool_executor=executions.execute,
            event_bus=event_bus,
        )
        driver = OwnerActivationDriver(
            database=database,
            run_owner=owner.run_activation,
            activations=activations,
        )
        self._service = ProjectRuntimeService(
            database=database,
            owner=owner,
            planning=planning,
            delivery=delivery,
            delivery_runner=delivery_runner,
            controls=controls,
            event_bus=event_bus,
            runtime_contexts=runtime_contexts,
            activations=activations,
            driver=driver,
        )

    def submit_message(self, content: str) -> OwnerActivation:
        """Persist user input and enqueue one durable Owner activation."""
        return self._service.submit_user_message(content)

    def approve_plan(self) -> PlanDecision:
        """Approve the pending Plan and enqueue the Owner decision input."""
        return self._service.approve_plan()

    def reject_plan(self, feedback: str = "") -> PlanDecision:
        """Reject the pending Plan and enqueue the Owner decision input."""
        return self._service.reject_plan(feedback)

    def drive_next_activation(self) -> ActivationDriveResult:
        """Claim and process one pending Owner activation."""
        return self._service.drive_next_activation()

    def start_first_run(self) -> MilestoneRunQueued:
        """Apply the first explicit Run approval and queue its first Stage."""
        return self._service.start_first_run()

    def drive_delivery(self) -> DeliveryDriveResult:
        """Run one queued Stage through the Delivery Driver."""
        return self._service.drive_delivery()

    def project_control_view(self) -> ProjectControlView:
        """Return the stable read model used by control clients and debug tooling."""
        return self._service.project_control_view()

    def execute_action(self, action: Action) -> ToolExecutionResult:
        """Execute one explicit tool action without entering the Agent loop."""
        return self._service.execute_action(action)
