"""Normal command facade for one composed Feature Runtime."""

from dataclasses import dataclass, field

from agentplanex.domains.owner_activation import OwnerActivation
from agentplanex.domains.project_runtime_state import ProjectRuntimeState
from agentplanex.services.delivery import MilestoneRunQueued
from agentplanex.services.planning import PlanDecision
from agentplanex.services.project_runtime import ProjectRuntimeService


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

    def drive_until_waiting(self) -> ProjectRuntimeState:
        return self._service.drive_until_waiting()

    def fail_interrupted_work(self) -> bool:
        return self._service.fail_interrupted_work()
