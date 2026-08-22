"""Persisted Project Owner Agent domain object."""

from dataclasses import dataclass

from agentplanex.project_owner_agent.context.models import MessageHistory, SummaryHistory


@dataclass(frozen=True, slots=True)
class ProjectOwnerAgent:
    """A persisted Project Owner Agent and its currently loaded history."""

    triage_id: str
    project_owner_session_id: str
    system_prompt: str
    tools: tuple[str, ...]
    summary_id: str | None = None
    message_id: str | None = None
    summary_history: SummaryHistory | None = None
    message_history: MessageHistory | None = None
