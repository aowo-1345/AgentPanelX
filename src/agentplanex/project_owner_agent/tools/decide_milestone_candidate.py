"""Definition of the decide_milestone_candidate tool; execution is Runtime-owned."""

from agentplanex.domains import ToolSchema
from agentplanex.project_owner_agent.tools.base import ToolDefinition

DECIDE_MILESTONE_CANDIDATE_TOOL_NAME = "decide_milestone_candidate"

DECIDE_MILESTONE_CANDIDATE_TOOL_SCHEMA: ToolSchema = {
    "type": "function",
    "name": DECIDE_MILESTONE_CANDIDATE_TOOL_NAME,
    "description": (
        "Accept or reject the exact current Milestone Candidate after inspecting its "
        "fixed Git evidence and any delegated review. Accept integrates it and records "
        "Milestone completion; reject preserves it for audit and leaves the Milestone "
        "unfinished."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "decision": {
                "type": "string",
                "enum": ["accept", "reject"],
            },
            "reason": {
                "type": "string",
                "description": "Why the current Candidate is accepted or rejected.",
            },
        },
        "required": ["decision", "reason"],
        "additionalProperties": False,
    },
    "strict": True,
}

DECIDE_MILESTONE_CANDIDATE_TOOL = ToolDefinition(
    name=DECIDE_MILESTONE_CANDIDATE_TOOL_NAME,
    schema=DECIDE_MILESTONE_CANDIDATE_TOOL_SCHEMA,
)
