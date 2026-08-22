"""Minimal Agent control loop adapted from Mini-SWE-Agent."""

from dataclasses import dataclass
from typing import Never

from agentplanex.project_owner_agent.context.manager import OwnerContextManager
from agentplanex.project_owner_agent.contracts import (
    AgentToolExecutor,
    Message,
    ToolExecutionResult,
)
from agentplanex.project_owner_agent.exception import (
    FormatError,
    RepeatedFormatError,
    ReplyToHuman,
    StepLimitExceeded,
    ToolRequestedExit,
)
from agentplanex.project_owner_agent.models.base import Model


@dataclass(frozen=True, slots=True)
class AgentConfig:
    step_limit: int = 20
    max_consecutive_format_errors: int = 3

    def __post_init__(self) -> None:
        if self.step_limit <= 0:
            raise ValueError("step_limit must be positive")
        if self.max_consecutive_format_errors <= 0:
            raise ValueError("max_consecutive_format_errors must be positive")


class DefaultAgent:
    def __init__(
        self,
        model: Model,
        execute_tool: AgentToolExecutor,
        *,
        owner_context: OwnerContextManager,
        config: AgentConfig,
    ) -> None:
        self.model = model
        self.execute_tool = execute_tool
        self.owner_context = owner_context
        self.config = config
        self.n_calls = 0
        self.n_consecutive_format_errors = 0

    def run(self, task: str = "") -> Never:
        if task:
            self.add_messages({"role": "user", "content": task})

        self.n_calls = 0
        self.n_consecutive_format_errors = 0

        while True:
            try:
                self.step()
                self.n_consecutive_format_errors = 0
            except FormatError as error:
                self.n_consecutive_format_errors += 1
                if (
                    self.n_consecutive_format_errors
                    >= self.config.max_consecutive_format_errors
                ):
                    raise RepeatedFormatError from error
                self.add_messages(
                    {
                        "role": "user",
                        "content": error.content,
                        "extra": {"response": error.response},
                    },
                )

    def step(self) -> list[Message]:
        """Query the model and execute its actions."""
        return self.execute_actions(self.query())

    def query(self) -> Message:
        """Query the model, persisting only a terminal reply here."""
        if self.n_calls >= self.config.step_limit:
            raise StepLimitExceeded()
        messages = list(self.owner_context.prepare_query(self.n_calls))
        self.n_calls += 1
        try:
            message = self.model.query(messages)
        except ReplyToHuman as error:
            self.add_messages(error.response)
            raise
        return message

    def execute_actions(
        self,
        message: Message,
    ) -> list[Message]:
        """Execute actions and append their provider-formatted observations."""
        self.add_messages(message)
        extra = message.get("extra")
        raw_actions = extra.get("actions", []) if isinstance(extra, dict) else []
        actions = [action for action in raw_actions if isinstance(action, dict)]
        results = [self.execute_tool(action) for action in actions]
        return self._record_action_results(message, results)

    def _record_action_results(
        self,
        message: Message,
        results: list[ToolExecutionResult],
    ) -> list[Message]:
        observations = self.model.format_observation_messages(
            message,
            [result.output for result in results],
        )
        appended = [message, *self.add_messages(*observations)]
        exits = [result.exit for result in results if result.exit is not None]
        if not exits:
            return appended
        if len(exits) != 1:
            raise RuntimeError("Multiple tool actions requested an Agent exit")

        raise ToolRequestedExit(exits[0])

    def add_messages(
        self,
        *messages: Message,
    ) -> list[Message]:
        appended = self.owner_context.append(messages)
        return list(appended)
