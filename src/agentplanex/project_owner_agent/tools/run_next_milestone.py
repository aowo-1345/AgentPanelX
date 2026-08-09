"""Definition of the run_next_milestone tool; execution is Runtime-owned."""

from agentplanex.domains import ToolSchema
from agentplanex.project_owner_agent.tools.base import ToolDefinition

RUN_NEXT_MILESTONE_TOOL_NAME = "run_next_milestone"

RUN_NEXT_MILESTONE_TOOL_SCHEMA: ToolSchema = {
    "type": "function",
    "name": RUN_NEXT_MILESTONE_TOOL_NAME,
    "description": (
        "Request the first unfinished Milestone from the current complete View. The first "
        "call requests explicit user Start approval; later calls queue delivery. After a "
        "terminal Stage failure, a BLOCKED project may retry the same first unfinished "
        "Milestone when the approved Plan and Snapshot remain valid."
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
