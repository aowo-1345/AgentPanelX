"""Privileged single-step control over one composed Feature Runtime."""

from dataclasses import dataclass, field

from agentplanex.domains.project_runtime_state import ProjectRuntimeState
from agentplanex.project_owner_agent.contracts import Action, ToolExecutionResult
from agentplanex.services.delivery.contracts import (
    DeliveryDriveOutcome,
    MilestoneRunQueued,
)
from agentplanex.services.planning.contracts import PlanDecision
from agentplanex.services.project_runtime import ProjectRuntimeService
from agentplanex.services.project_runtime_context._activation import (
    ActivationDriveResult,
    ToolActivationDriveResult,
)
from agentplanex.services.project_runtime_context.context import ProjectRuntimeContext
from agentplanex.services.project_runtime_context.models import OwnerActivation


@dataclass(eq=False, slots=True)
class ProjectRuntimeControl:
    """Expose explicit human intervention without defining another state path."""

    _service: ProjectRuntimeService = field(repr=False)
    _context: ProjectRuntimeContext = field(repr=False)

    def initialize(self) -> ProjectRuntimeState:
        return self._service.initialize()

    def submit_message(self, content: str) -> OwnerActivation:
        return self._service.submit_user_message(content)

    def approve_plan(self) -> PlanDecision:
        return self._service.approve_plan()

    def reject_plan(self, feedback: str = "") -> PlanDecision:
        return self._service.reject_plan(feedback)

    def start_first_run(self) -> MilestoneRunQueued:
        return self._service.start_first_run()

    def drive_delivery(self) -> DeliveryDriveOutcome:
        return self._service.drive_delivery()

    def drive_owner_model(self) -> ActivationDriveResult:
        return self._context.drive_owner()

    def drive_owner_tool(self, action: Action) -> ToolActivationDriveResult:
        return self._context.drive_owner_tool(action)

    def reply_owner(self, content: str) -> ToolActivationDriveResult:
        return self._context.reply_owner(content)

    def fail_owner(self, reason: str) -> ToolActivationDriveResult:
        return self._context.fail_owner(reason)

    def execute_tool(self, action: Action) -> ToolExecutionResult:
        return self._context.execute_tool(action)
