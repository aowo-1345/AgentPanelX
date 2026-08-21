"""Project-bound tool execution composition."""

from pathlib import Path

from agentplanex.project_runtime.executions import bash as _bash  # noqa: F401
from agentplanex.project_runtime.executions import (  # noqa: F401
    decide_milestone_candidate as _decide_milestone_candidate,
)
from agentplanex.project_runtime.executions import (  # noqa: F401
    request_plan_approval as _request_plan_approval,
)
from agentplanex.project_runtime.executions import (  # noqa: F401
    run_next_milestone as _run_next_milestone,
)
from agentplanex.project_runtime.executions import talk_to_agent as _talk_to_agent  # noqa: F401
from agentplanex.project_runtime.executions import (
    update_milestones as _update_milestones,  # noqa: F401
)
from agentplanex.project_runtime.executions.base import (
    ProjectExecutionDependencies,
    ProjectExecutions,
)
from agentplanex.services.agent_collaboration import AgentCollaborationService
from agentplanex.services.delivery import DeliveryService
from agentplanex.services.event_bus import EventBus
from agentplanex.services.planning import PlanningService
from agentplanex.services.project_runtime_context import ProjectRuntimeContext
from agentplanex.settings import RuntimeSettings


def create_project_executions(
    project_path: Path,
    settings: RuntimeSettings,
    *,
    context: ProjectRuntimeContext,
    planning: PlanningService,
    delivery: DeliveryService,
    collaboration: AgentCollaborationService,
    event_bus: EventBus,
) -> ProjectExecutions:
    """Create the Tool catalog inside one explicit Runtime composition graph."""
    return ProjectExecutions(
        ProjectExecutionDependencies(
            project_path=project_path,
            settings=settings,
            planning=planning,
            delivery=delivery,
            collaboration=collaboration,
            event_bus=event_bus,
            context=context,
        )
    )


__all__ = ["ProjectExecutions", "create_project_executions"]
