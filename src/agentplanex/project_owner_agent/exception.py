"""Structured control-flow and provider errors for the Project Owner Agent."""

from agentplanex.domains.agent_exit import AgentExit, AgentExitStatus
from agentplanex.project_owner_agent.models.base import Message


class InterruptAgentFlow(Exception):
    """Base class for structured interruptions of the Agent flow."""

    def __init__(self, *, content: str) -> None:
        super().__init__(content)
        self.content = content


class FormatError(InterruptAgentFlow):
    """The model returned a response that the Agent can ask it to repair."""

    def __init__(self, *, content: str, response: Message) -> None:
        super().__init__(content=content)
        self.response = response


class AgentFlowExit(InterruptAgentFlow):
    """A terminal Agent interruption with a runtime-visible status."""

    def __init__(
        self,
        *,
        status: AgentExitStatus,
        content: str,
    ) -> None:
        super().__init__(content=content)
        self.status = status


class ReplyToHuman(AgentFlowExit):
    """The model produced a final natural-language reply."""

    def __init__(self, content: str, response: Message) -> None:
        super().__init__(
            status=AgentExitStatus.REPLY_TO_HUMAN,
            content=content,
        )
        self.response = response


class StepLimitExceeded(AgentFlowExit):
    """The Agent reached its configured model-call limit."""

    def __init__(self) -> None:
        super().__init__(
            status=AgentExitStatus.STEP_LIMIT_EXCEEDED,
            content=AgentExitStatus.STEP_LIMIT_EXCEEDED.value,
        )


class RepeatedFormatError(AgentFlowExit):
    """The model repeatedly returned responses that cannot drive the Agent."""

    def __init__(self) -> None:
        super().__init__(
            status=AgentExitStatus.REPEATED_FORMAT_ERROR,
            content=AgentExitStatus.REPEATED_FORMAT_ERROR.value,
        )


class ToolRequestedExit(AgentFlowExit):
    """A completed tool execution requested that the Agent Loop stop."""

    def __init__(self, agent_exit: AgentExit) -> None:
        super().__init__(status=agent_exit.status, content=agent_exit.content)


class JBBModelError(RuntimeError):
    """The JBB request or response failed outside model-controlled formatting."""
