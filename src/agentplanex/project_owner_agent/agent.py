"""Minimal Agent control loop adapted from Mini-SWE-Agent."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Never

from agentplanex.domains import (
    ProjectRuntimeContext,
    ToolExecutionResult,
    ToolExecutor,
)
from agentplanex.project_owner_agent.exception import (
    FormatError,
    RepeatedFormatError,
    ReplyToHuman,
    StepLimitExceeded,
    ToolRequestedExit,
)
from agentplanex.project_owner_agent.models.base import Message, Model

type MessageAppender = Callable[
    [ProjectRuntimeContext, tuple[Message, ...]], None
]
type QueryPreparer = Callable[
    [ProjectRuntimeContext, int, Sequence[Message]], Sequence[Message]
]


def _unchanged_query(
    _context: ProjectRuntimeContext,
    _query_index: int,
    messages: Sequence[Message],
) -> Sequence[Message]:
    return messages


@dataclass(frozen=True, slots=True)
class AgentConfig:
    system_prompt: str
    step_limit: int = 20
    max_consecutive_format_errors: int = 3

    def __post_init__(self) -> None:
        if not self.system_prompt.strip():
            raise ValueError("system_prompt must not be empty")
        if self.step_limit <= 0:
            raise ValueError("step_limit must be positive")
        if self.max_consecutive_format_errors <= 0:
            raise ValueError("max_consecutive_format_errors must be positive")


class DefaultAgent:
    def __init__(
        self,
        model: Model,
        execute_tool: ToolExecutor,
        *,
        append_messages: MessageAppender,
        initial_messages: Sequence[Message] = (),
        prepare_query: QueryPreparer = _unchanged_query,
        config: AgentConfig,
    ) -> None:
        self.model = model
        self.execute_tool = execute_tool
        self.append_persisted_messages = append_messages
        self.prepare_query_messages = prepare_query
        self.config = config
        self.messages = [dict(message) for message in initial_messages]
        self.n_calls = 0
        self.n_consecutive_format_errors = 0

    def run(self, context: ProjectRuntimeContext, task: str = "") -> Never:
        initial: list[Message] = []
        if not self.messages:
            initial.append(
                {"role": "system", "content": self.config.system_prompt}
            )
        if task:
            initial.append({"role": "user", "content": task})
        if initial:
            self.add_messages(context, *initial)
        if not self.messages:
            raise ValueError("Agent has no message history or new task")

        self.n_calls = 0
        self.n_consecutive_format_errors = 0

        while True:
            try:
                self.step(context)
                self.n_consecutive_format_errors = 0
            except FormatError as error:
                self.n_consecutive_format_errors += 1
                if (
                    self.n_consecutive_format_errors
                    >= self.config.max_consecutive_format_errors
                ):
                    raise RepeatedFormatError from error
                self.add_messages(
                    context,
                    {
                        "role": "user",
                        "content": error.content,
                        "extra": {"response": error.response},
                    },
                )

    def step(self, context: ProjectRuntimeContext) -> list[Message]:
        """Query the model and execute its actions."""
        return self.execute_actions(context, self.query(context))

    def query(self, context: ProjectRuntimeContext) -> Message:
        """Query the model, persisting only a terminal reply here."""
        if self.n_calls >= self.config.step_limit:
            raise StepLimitExceeded()
        self.messages = [
            dict(message)
            for message in self.prepare_query_messages(
                context,
                self.n_calls,
                self.messages,
            )
        ]
        self.n_calls += 1
        try:
            message = self.model.query(self.messages)
        except ReplyToHuman as error:
            self.add_messages(context, error.response)
            raise
        return message

    def execute_actions(
        self,
        context: ProjectRuntimeContext,
        message: Message,
    ) -> list[Message]:
        """Execute actions and append their provider-formatted observations."""
        self.add_messages(context, message)
        extra = message.get("extra")
        raw_actions = extra.get("actions", []) if isinstance(extra, dict) else []
        actions = [action for action in raw_actions if isinstance(action, dict)]
        results = [self.execute_tool(context, action) for action in actions]
        return self._record_action_results(context, message, results)

    def _record_action_results(
        self,
        context: ProjectRuntimeContext,
        message: Message,
        results: list[ToolExecutionResult],
    ) -> list[Message]:
        observations = self.model.format_observation_messages(
            message,
            [result.output for result in results],
        )
        appended = [message, *self.add_messages(context, *observations)]
        exits = [result.exit for result in results if result.exit is not None]
        if not exits:
            return appended
        if len(exits) != 1:
            raise RuntimeError("Multiple tool actions requested an Agent exit")

        raise ToolRequestedExit(exits[0])

    def add_messages(
        self,
        context: ProjectRuntimeContext,
        *messages: Message,
    ) -> list[Message]:
        appended = tuple(dict(message) for message in messages)
        self.append_persisted_messages(context, appended)
        self.messages.extend(appended)
        return list(appended)
