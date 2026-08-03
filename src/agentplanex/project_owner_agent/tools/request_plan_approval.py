"""Definition of the request_plan_approval tool; execution is Runtime-owned."""

from agentplanex.domains import ToolSchema
from agentplanex.project_owner_agent.tools.base import ToolDefinition

REQUEST_PLAN_APPROVAL_TOOL_NAME = "request_plan_approval"

REQUEST_PLAN_APPROVAL_TOOL_SCHEMA: ToolSchema = {
    "type": "function",
    "name": REQUEST_PLAN_APPROVAL_TOOL_NAME,
    "description": (
        "Request human approval for the current architecture.md, requirements.md, "
        "and roadmap.md specification documents."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
    "strict": True,
}

REQUEST_PLAN_APPROVAL_TOOL = ToolDefinition(
    name=REQUEST_PLAN_APPROVAL_TOOL_NAME,
    schema=REQUEST_PLAN_APPROVAL_TOOL_SCHEMA,
)
