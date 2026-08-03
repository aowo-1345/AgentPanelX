"""Control-flow and provider errors for the Project Owner Agent."""

from agentplanex.project_owner_agent.models.base import Message


class InterruptAgentFlow(Exception):
    """Interrupt the current step and append the supplied messages."""

    def __init__(self, *messages: Message) -> None:
        super().__init__()
        self.messages = list(messages)


class FormatError(InterruptAgentFlow):
    """The model returned a response that cannot drive the Agent loop."""


class StepLimitExceeded(InterruptAgentFlow):
    """The Agent reached its configured model-call limit."""


class ReplyToHuman(InterruptAgentFlow):
    """The model produced a final natural-language reply."""


class JBBModelError(RuntimeError):
    """The JBB request or response failed outside model-controlled formatting."""
