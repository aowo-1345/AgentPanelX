"""Model-facing Bash tool definition."""

from agentplanex.domains import BASH_TOOL_NAME, ToolSchema
from agentplanex.project_owner_agent.tools.base import ToolDefinition

BASH_TOOL_SCHEMA: ToolSchema = {
    "type": "function",
    "name": BASH_TOOL_NAME,
    "description": "Execute a Bash command in the current working directory.",
    "parameters": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The Bash command to execute.",
            }
        },
        "required": ["command"],
        "additionalProperties": False,
    },
    "strict": True,
}

BASH_TOOL = ToolDefinition(name=BASH_TOOL_NAME, schema=BASH_TOOL_SCHEMA)
