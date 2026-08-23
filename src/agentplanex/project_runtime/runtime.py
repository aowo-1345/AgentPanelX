"""Normal command facade for one composed Feature Runtime."""

from dataclasses import dataclass, field

from agentplanex.domains.project_runtime_state import ProjectRuntimeState
from agentplanex.services.delivery.contracts import MilestoneRunQueued
from agentplanex.services.planning.contracts import PlanDecision
from agentplanex.services.project_runtime import ProjectRuntimeService
from agentplanex.services.project_runtime_context.models import OwnerActivation


@dataclass(eq=False, slots=True)
class ProjectRuntime:
    """Expose only normal Feature commands over one Runtime Service."""

    _service: ProjectRuntimeService = field(repr=False)

    def initialize(self) -> ProjectRuntimeState:
        return self._service.initialize()

    def state(self) -> ProjectRuntimeState:
        return self._service.state()

    def begin_feature(self) -> ProjectRuntimeState:
        return self._service.begin_feature()

    def submit_message(self, content: str) -> OwnerActivation:
        return self._service.submit_user_message(content)

    def approve_plan(self) -> PlanDecision:
        return self._service.approve_plan()

    def reject_plan(self, feedback: str = "") -> PlanDecision:
        return self._service.reject_plan(feedback)

    def start_first_run(self) -> MilestoneRunQueued:
        return self._service.start_first_run()

    def approve_blocked_run(self) -> MilestoneRunQueued:
        return self._service.approve_blocked_run()

    def reject_blocked_run(self, feedback: str) -> ProjectRuntimeState:
        return self._service.reject_blocked_run(feedback)

    def drive_until_waiting(self) -> ProjectRuntimeState:
        return self._service.drive_until_waiting()

    def fail_interrupted_work(self) -> bool:
        return self._service.fail_interrupted_work()
