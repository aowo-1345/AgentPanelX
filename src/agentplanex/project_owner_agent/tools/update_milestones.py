"""Definition of the update_milestones tool; execution is Runtime-owned."""

from agentplanex.domains import ToolSchema
from agentplanex.project_owner_agent.tools.base import ToolDefinition

UPDATE_MILESTONES_TOOL_NAME = "update_milestones"

UPDATE_MILESTONES_TOOL_SCHEMA: ToolSchema = {
    "type": "function",
    "name": UPDATE_MILESTONES_TOOL_NAME,
    "description": "Replace the complete Milestone View for the current project.",
    "parameters": {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "description": "Why the complete Milestone View is being updated.",
            },
            "milestones": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string"},
                        "objective": {"type": "string"},
                        "state": {
                            "type": "string",
                            "enum": ["pending", "completed"],
                        },
                        "stages": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "key": {"type": "string"},
                                    "objective": {"type": "string"},
                                },
                                "required": ["key", "objective"],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "required": ["key", "objective", "state", "stages"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["reason", "milestones"],
        "additionalProperties": False,
    },
    "strict": True,
}

UPDATE_MILESTONES_TOOL = ToolDefinition(
    name=UPDATE_MILESTONES_TOOL_NAME,
    schema=UPDATE_MILESTONES_TOOL_SCHEMA,
)
