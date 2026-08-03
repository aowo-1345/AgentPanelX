"""Definition of the request_plan_approval tool; execution is Runtime-owned."""

from agentplanex.domains import ToolSchema
from agentplanex.project_owner_agent.tools.base import ToolDefinition

REQUEST_PLAN_APPROVAL_TOOL_NAME = "request_plan_approval"

REQUEST_PLAN_APPROVAL_TOOL_SCHEMA: ToolSchema = {
    "type": "function",
    "name": REQUEST_PLAN_APPROVAL_TOOL_NAME,
    "description": (
        "Commit the updated local specification documents as a named Plan version "
        "and request human approval."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "version_name": {
                "type": "string",
                "description": "Human-readable name for the new Plan version.",
            }
        },
        "required": ["version_name"],
        "additionalProperties": False,
    },
    "strict": True,
}

REQUEST_PLAN_APPROVAL_TOOL = ToolDefinition(
    name=REQUEST_PLAN_APPROVAL_TOOL_NAME,
    schema=REQUEST_PLAN_APPROVAL_TOOL_SCHEMA,
)
