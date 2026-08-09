"""Config-rendered definition of the blocking talk_to_agent tool."""

from agentplanex.domains import ToolSchema
from agentplanex.project_owner_agent.tools.base import ToolDefinition

TALK_TO_AGENT_TOOL_NAME = "talk_to_agent"


def create_talk_to_agent_tool(agent_cards: str) -> ToolDefinition:
    """Render current Agent Cards into a structurally stable tool schema."""
    schema: ToolSchema = {
        "type": "function",
        "name": TALK_TO_AGENT_TOOL_NAME,
        "description": (
            "Synchronously send a Message or file-producing Task to a configured "
            "Planner or Reviewer. Message is a discussion turn with no document; Task "
            "publishes the role Contract document (Planner plan.md or Reviewer review.md). "
            "Reuse conversation_id for follow-up work and pass returned artifact URIs as "
            "read-only inputs. Planner output is advisory until the Owner adopts it into "
            "canonical Specs; Reviewer output is evidence and never makes the Owner's "
            "decision. Available Agent Cards:\n"
            f"{agent_cards}"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": f"Target Config Agent ID. Available Cards:\n{agent_cards}",
                },
                "kind": {
                    "type": "string",
                    "enum": ["message", "task"],
                    "description": (
                        "Use message for discussion and task when the Agent must publish "
                        "its role Contract document through Outbox."
                    ),
                },
                "message": {
                    "type": "string",
                    "description": "The message or task instructions for the target Agent.",
                },
                "conversation_id": {
                    "type": "string",
                    "description": (
                        "Opaque ID returned by an earlier call. Omit it to start a new "
                        "Agent conversation and workspace."
                    ),
                },
                "artifacts": {
                    "type": "array",
                    "description": (
                        "Project or Agent Artifact URIs supplied as read-only inputs."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {"uri": {"type": "string"}},
                        "required": ["uri"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["agent_id", "kind", "message", "artifacts"],
            "additionalProperties": False,
        },
        "strict": True,
    }
    return ToolDefinition(name=TALK_TO_AGENT_TOOL_NAME, schema=schema)


TALK_TO_AGENT_TOOL = create_talk_to_agent_tool(
    "- planner (planner)\n- reviewer (reviewer)"
)
