"""Privileged single-step control over one composed Feature Runtime."""

from dataclasses import dataclass

from agentplanex.domains import Action, OwnerActivation, ToolExecutionResult
from agentplanex.services.delivery import DeliveryDriveOutcome, MilestoneRunQueued
from agentplanex.services.planning import PlanDecision
from agentplanex.services.project_runtime import ProjectRuntimeService
from agentplanex.services.project_runtime_context import (
    ActivationDriveResult,
    ProjectRuntimeContext,
    ToolActivationDriveResult,
)


@dataclass(slots=True)
class ProjectRuntimeControl:
    """Expose explicit human intervention without defining another state path."""

    _service: ProjectRuntimeService
    _context: ProjectRuntimeContext

    def submit_message(self, content: str) -> OwnerActivation:
        with self._context.operation():
            return self._service.submit_user_message(content)

    def approve_plan(self) -> PlanDecision:
        with self._context.operation():
            return self._service.approve_plan()

    def reject_plan(self, feedback: str = "") -> PlanDecision:
        with self._context.operation():
            return self._service.reject_plan(feedback)

    def start_first_run(self) -> MilestoneRunQueued:
        with self._context.operation():
            return self._service.start_first_run()

    def drive_delivery(self) -> DeliveryDriveOutcome:
        with self._context.operation():
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
