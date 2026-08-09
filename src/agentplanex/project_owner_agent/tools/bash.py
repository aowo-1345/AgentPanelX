"""Model-facing Bash tool definition."""

from agentplanex.domains import BASH_TOOL_NAME, ToolSchema
from agentplanex.project_owner_agent.tools.base import ToolDefinition

BASH_TOOL_SCHEMA: ToolSchema = {
    "type": "function",
    "name": BASH_TOOL_NAME,
    "description": (
        "Execute a Bash command with writes confined to the current Feature worktree, "
        "with .git and .agentplanex read-only and network access disabled. If the "
        "sandbox denies a required capability, do not retry or attempt a bypass; "
        "explain the required user action and return control to the user."
    ),
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
