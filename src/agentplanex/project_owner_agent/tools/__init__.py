"""Model-facing tool definitions for the Project Owner Agent."""

from agentplanex.project_owner_agent.tools.base import ToolCatalog, ToolDefinition
from agentplanex.project_owner_agent.tools.bash import BASH_TOOL
from agentplanex.project_owner_agent.tools.request_plan_approval import REQUEST_PLAN_APPROVAL_TOOL
from agentplanex.project_owner_agent.tools.talk_to_agent import (
    TALK_TO_AGENT_TOOL,
    create_talk_to_agent_tool,
)

__all__ = [
    "BASH_TOOL",
    "REQUEST_PLAN_APPROVAL_TOOL",
    "TALK_TO_AGENT_TOOL",
    "ToolCatalog",
    "ToolDefinition",
    "create_talk_to_agent_tool",
]
