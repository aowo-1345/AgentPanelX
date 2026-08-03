"""Definition of the run_next_milestone tool; execution is Runtime-owned."""

from agentplanex.domains import ToolSchema
from agentplanex.project_owner_agent.tools.base import ToolDefinition

RUN_NEXT_MILESTONE_TOOL_NAME = "run_next_milestone"

RUN_NEXT_MILESTONE_TOOL_SCHEMA: ToolSchema = {
    "type": "function",
    "name": RUN_NEXT_MILESTONE_TOOL_NAME,
    "description": (
        "Run the first unfinished Milestone when no earlier Candidate is awaiting a decision."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    },
    "strict": True,
}

RUN_NEXT_MILESTONE_TOOL = ToolDefinition(
    name=RUN_NEXT_MILESTONE_TOOL_NAME,
    schema=RUN_NEXT_MILESTONE_TOOL_SCHEMA,
)
