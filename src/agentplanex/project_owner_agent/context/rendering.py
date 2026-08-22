"""Pure rendering of persisted Owner facts into model messages."""

from collections.abc import Sequence

from agentplanex.project_owner_agent.context.models import MessageHistory, SummaryHistory
from agentplanex.project_owner_agent.contracts import Message


def render_checkpoint(
    *,
    system_prompt: str,
    summary: SummaryHistory | None,
    message_history: Sequence[MessageHistory],
    summary_context_header: str,
) -> tuple[Message, ...]:
    """Render persisted checkpoint facts without a live invocation envelope."""

    rendered: list[Message] = [
        {"role": "system", "content": system_prompt.strip()}
    ]
    if summary is not None:
        rendered.extend(render_summary(summary, summary_context_header))
    rendered.extend(
        dict(message)
        for history in message_history
        for message in history.message
        if message.get("role") != "system"
    )
    return tuple(rendered)


def render_summary(
    summary: SummaryHistory,
    header: str,
) -> tuple[Message, Message]:
    """Render one immutable Summary as model-visible context."""

    return (
        {"role": "developer", "content": header.strip()},
        {
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": (
                        "<intent-summary>\n"
                        f"{summary.intent_summary_content}\n"
                        "</intent-summary>"
                    ),
                },
                {
                    "type": "input_text",
                    "text": (
                        "<trajectory-summary>\n"
                        f"{summary.trajectory_summary_content}\n"
                        "</trajectory-summary>"
                    ),
                },
            ],
        },
    )
