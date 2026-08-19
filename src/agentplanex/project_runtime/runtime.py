"""Project-scoped Runtime entry point and tool environment."""

from pathlib import Path

from agentplanex.domains import (
    Action,
    OwnerActivation,
    ProjectRuntimeContext,
    ToolExecutionResult,
)
from agentplanex.infrastructure.git_repository import GitRepository
from agentplanex.infrastructure.sqlite import SQLiteDatabase, initialize_schema
from agentplanex.infrastructure.sqlite.repositories import (
    SQLiteOwnerActivationRepository,
)
from agentplanex.infrastructure.sqlite.timeline import SQLiteTimelineRecorder
from agentplanex.project_owner_agent.approval import ApprovalMode
from agentplanex.project_owner_agent.models.jbb import (
    JBBResponses,
    OpenAIResponsesTransport,
    ResponsesTransport,
)
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
    ToolActivationDriveResult,
)
from agentplanex.services.agent_contracts import resolve_observation_skill
from agentplanex.services.delivery import MilestoneRunQueued
from agentplanex.services.delivery_runner import DeliveryDriveResult, DeliveryRunner
from agentplanex.services.owner_activation import (
    ActivationDriveResult,
    OwnerActivationDriver,
)
from agentplanex.services.owner_context import ProjectOwnerContextQuery
from agentplanex.services.owner_context_memory import ProjectOwnerContextMemory
from agentplanex.services.plan_hard_gate import CodexPlanHardGate
from agentplanex.services.planning import PlanDecision
from agentplanex.services.project_workspace import (
    ProjectWorkspaceQuery,
    ProjectWorkspaceView,
)
from agentplanex.services.stage_executor import CodexStageExecutor, StageExecutor
from agentplanex.settings import Settings


class ProjectRuntime:
    """Expose one persisted Project Owner through project-scoped commands."""

    def __init__(
        self,
        *,
        project_path: Path,
        settings: Settings,
        approval_mode: ApprovalMode,
        responses_transport: ResponsesTransport | None = None,
        stage_executor: StageExecutor | None = None,
    ) -> None:
        project_path = project_path.resolve()
        if not project_path.is_dir():
            raise ValueError(f"Project path is not a directory: {project_path}")
        observation_skill = resolve_observation_skill()

        database = SQLiteDatabase.for_project(project_path)
        initialize_schema(database)
        event_bus = EventBus((SQLiteTimelineRecorder(database),))
        model_settings = settings.project_owner_agent.selected_model
        transport = (
            responses_transport
            if responses_transport is not None
            else OpenAIResponsesTransport(
                base_url=model_settings.base_url,
                timeout_seconds=model_settings.timeout_seconds,
                api_key_env=model_settings.api_key_env,
                http_headers=model_settings.http_headers,
                reasoning_effort=model_settings.reasoning_effort,
                service_tier=model_settings.service_tier,
            )
        )
        responses = JBBResponses(model=model_settings.name, transport=transport)
        runtime_contexts = RuntimeContextService(database, event_bus)
        activations = SQLiteOwnerActivationRepository()
        collaboration = AgentCollaborationService.from_settings(
            project_path,
            settings.runtime,
            observation_skill=observation_skill,
        )
        owner_contexts = ProjectOwnerContextQuery(
            database,
            collaboration.prompts.summary_context_header,
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
        git = GitRepository(project_path)
        delivery = DeliveryService(
            project_path=project_path,
            database=database,
            event_bus=event_bus,
            runtime_contexts=runtime_contexts,
            git=git,
            review_milestones=hard_gate.review_milestones,
        )
        delivery_runner = DeliveryRunner(
            delivery=delivery,
            executor=(
                stage_executor
                if stage_executor is not None
                else CodexStageExecutor(
                    project_path,
                    collaboration.transport,
                    collaboration.observation_skill,
                    collaboration.prompts,
                )
            ),
            git=git,
        )
        controls = ProjectControlQuery(
            database=database,
            git=git,
        )
        self._workspace_query = ProjectWorkspaceQuery(database=database, git=git)
        executions = create_project_executions(
            project_path,
            settings.runtime,
            planning,
            delivery,
            collaboration,
            event_bus,
        )
        context_memory = ProjectOwnerContextMemory(
            database=database,
            settings=settings,
            tools=executions.tools,
            responses=responses,
            event_bus=event_bus,
        )
        owner = ProjectOwnerService(
            database=database,
            settings=settings,
            approval_mode=approval_mode,
            tools=executions.tools,
            tool_executor=executions.execute,
            event_bus=event_bus,
            owner_contexts=owner_contexts,
            context_memory=context_memory,
            responses=responses,
            observation_skill=collaboration.observation_skill,
            prompts=collaboration.prompts,
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
        self._git = git

    def initialize(self) -> ProjectRuntimeContext:
        """Initialize this Feature Runtime without messages, activations, or models."""
        context = self._service.initialize()
        self._git.ensure_runtime_excluded()
        return context

    def begin_feature(self, triage_id: str) -> ProjectRuntimeContext:
        """Begin one selected Feature without creating an Owner activation."""
        return self._service.begin_feature(triage_id)

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

    def drive_until_waiting(self) -> ProjectRuntimeContext:
        """Run durable automatic work until control must return to the user."""
        return self._service.drive_until_waiting()

    def fail_interrupted_work(self) -> bool:
        """Fail unfinished automatic work left by a stopped Runtime process."""
        return self._service.fail_interrupted_work()

    def drive_activation_tool(self, action: Action) -> ToolActivationDriveResult:
        """Drive one Owner activation step with a supplied Tool Action."""

        return self._service.drive_activation_tool(action)

    def reply_to_activation(self, content: str) -> ToolActivationDriveResult:
        """Finish a Tool-driven Owner activation with a persisted reply."""

        return self._service.reply_to_activation(content)

    def fail_activation(self, reason: str) -> ToolActivationDriveResult:
        """Explicitly fail a Tool-driven Owner activation."""

        return self._service.fail_activation(reason)

    def start_first_run(self) -> MilestoneRunQueued:
        """Apply the first explicit Run approval and queue its first Stage."""
        return self._service.start_first_run()

    def drive_delivery(self) -> DeliveryDriveResult:
        """Run one queued Stage through the Delivery Driver."""
        return self._service.drive_delivery()

    def project_control_view(self) -> ProjectControlView:
        """Return the stable read model used by control clients and debug tooling."""
        return self._service.project_control_view()

    def project_workspace_view(self, triage_id: str) -> ProjectWorkspaceView:
        """Return independently degradable panels for one Web workspace."""
        return self._workspace_query.get(triage_id)

    def execute_action(self, action: Action) -> ToolExecutionResult:
        """Execute one explicit tool action without entering the Agent loop."""
        return self._service.execute_action(action)
