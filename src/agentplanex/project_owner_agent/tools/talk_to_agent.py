"""Definition of the talk_to_agent tool; collaboration execution is not implemented here."""

from agentplanex.domains import ToolSchema
from agentplanex.project_owner_agent.tools.base import ToolDefinition

TALK_TO_AGENT_TOOL_NAME = "talk_to_agent"

TALK_TO_AGENT_TOOL_SCHEMA: ToolSchema = {
    "type": "function",
    "name": TALK_TO_AGENT_TOOL_NAME,
    "description": "Send a message or Artifact-producing task to a local Planner or Reviewer.",
    "parameters": {
        "type": "object",
        "properties": {
            "role": {
                "type": "string",
                "enum": ["planner", "reviewer"],
            },
            "kind": {
                "type": "string",
                "enum": ["message", "task"],
            },
            "message": {
                "type": "string",
                "description": "The message or task instructions for the target Agent.",
            },
            "artifacts": {
                "type": "array",
                "description": "Local Markdown Artifact references supplied to the target Agent.",
                "items": {
                    "type": "object",
                    "properties": {
                        "uri": {"type": "string"},
                        "filename": {"type": "string"},
                    },
                    "required": ["uri", "filename"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["role", "kind", "message", "artifacts"],
        "additionalProperties": False,
    },
    "strict": True,
}

TALK_TO_AGENT_TOOL = ToolDefinition(
    name=TALK_TO_AGENT_TOOL_NAME,
    schema=TALK_TO_AGENT_TOOL_SCHEMA,
)
