"""Ephemeral, Tool-free interrogation of one restored Owner checkpoint."""

from dataclasses import dataclass, field

from agentplanex.domains import (
    HistoricalOwnerExchange,
    HistoricalOwnerFidelity,
    Message,
    RestoredOwnerContext,
)
from agentplanex.project_owner_agent.exception import ReplyToHuman
from agentplanex.project_owner_agent.models.base import Model
from agentplanex.services.agent_contracts import AgentPromptCatalog, PromptRole
from agentplanex.services.owner_context import ProjectOwnerContextQuery


@dataclass(slots=True)
class HistoricalOwnerFork:
    """One in-memory multi-turn witness session over restored context."""

    context: RestoredOwnerContext
    model: Model
    model_name: str
    role_instructions: str
    fidelity: HistoricalOwnerFidelity = field(default_factory=HistoricalOwnerFidelity)
    _messages: list[Message] = field(init=False, repr=False)
    _transcript: list[HistoricalOwnerExchange] = field(
        default_factory=list,
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        if not self.model_name.strip():
            raise ValueError("model_name must not be empty")
        self._messages = [dict(message) for message in self.context.messages]
        system = self._messages[0]
        if system.get("role") != "system":
            raise ValueError("Restored Owner context must start with System Prompt")
        original = str(system.get("content", "")).strip()
        system["content"] = f"{original}\n\n{self.role_instructions.strip()}"

    @property
    def transcript(self) -> tuple[HistoricalOwnerExchange, ...]:
        return tuple(self._transcript)

    def ask(self, question: str) -> HistoricalOwnerExchange:
        """Ask one question and commit only a successful in-memory exchange."""

        content = question.strip()
        if not content:
            raise ValueError("Historical Owner question must not be empty")
        question_message: Message = {"role": "user", "content": content}
        request = [*self._messages, question_message]
        try:
            self.model.query(request)
        except ReplyToHuman as reply:
            exchange = HistoricalOwnerExchange(
                turn=len(self._transcript) + 1,
                question=content,
                answer=reply.content,
            )
            self._messages.extend((question_message, dict(reply.response)))
            self._transcript.append(exchange)
            return exchange
        raise RuntimeError(
            "Historical Owner Fork must return a natural-language witness answer"
        )


@dataclass(slots=True)
class HistoricalOwnerForkService:
    """Open isolated Fork sessions from the shared read-only Context Query."""

    contexts: ProjectOwnerContextQuery
    prompts: AgentPromptCatalog

    def open(
        self,
        message_id: str,
        *,
        summary_id: str | None,
        model: Model,
        model_name: str,
    ) -> HistoricalOwnerFork:
        return HistoricalOwnerFork(
            context=self.contexts.restore(message_id, summary_id=summary_id),
            model=model,
            model_name=model_name,
            role_instructions=self.prompts.role_instructions(
                PromptRole.HISTORICAL_OWNER
            ),
        )
