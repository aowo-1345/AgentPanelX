"""Model-facing tool definitions for the Project Owner Agent."""

from agentplanex.project_owner_agent.tools.base import ToolCatalog, ToolDefinition
from agentplanex.project_owner_agent.tools.bash import BASH_TOOL
from agentplanex.project_owner_agent.tools.decide_milestone_candidate import (
    DECIDE_MILESTONE_CANDIDATE_TOOL,
)
from agentplanex.project_owner_agent.tools.request_plan_approval import REQUEST_PLAN_APPROVAL_TOOL
from agentplanex.project_owner_agent.tools.run_next_milestone import (
    RUN_NEXT_MILESTONE_TOOL,
)
from agentplanex.project_owner_agent.tools.talk_to_agent import (
    TALK_TO_AGENT_TOOL,
    create_talk_to_agent_tool,
)
from agentplanex.project_owner_agent.tools.update_milestones import (
    UPDATE_MILESTONES_TOOL,
)

__all__ = [
    "BASH_TOOL",
    "DECIDE_MILESTONE_CANDIDATE_TOOL",
    "REQUEST_PLAN_APPROVAL_TOOL",
    "RUN_NEXT_MILESTONE_TOOL",
    "TALK_TO_AGENT_TOOL",
    "UPDATE_MILESTONES_TOOL",
    "ToolCatalog",
    "ToolDefinition",
    "create_talk_to_agent_tool",
]
