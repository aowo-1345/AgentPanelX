"""Minimal Agent control loop adapted from Mini-SWE-Agent."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from agentplanex.domains import ProjectRuntimeContext, ToolExecutor
from agentplanex.project_owner_agent.exception import (
    FormatError,
    InterruptAgentFlow,
    StepLimitExceeded,
)
from agentplanex.project_owner_agent.models.base import Message, Model

type MessageAppender = Callable[
    [ProjectRuntimeContext, tuple[Message, ...]], None
]


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
        config: AgentConfig,
    ) -> None:
        self.model = model
        self.execute_tool = execute_tool
        self.append_persisted_messages = append_messages
        self.config = config
        self.messages = [dict(message) for message in initial_messages]
        self.n_calls = 0
        self.n_consecutive_format_errors = 0

    def run(self, context: ProjectRuntimeContext, task: str = "") -> Message:
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
                    self.add_messages(
                        context,
                        {
                            "role": "exit",
                            "content": "RepeatedFormatError",
                            "extra": {
                                "exit_status": "RepeatedFormatError",
                                "submission": "",
                            },
                        }
                    )
                else:
                    self.add_messages(context, *error.messages)
            except InterruptAgentFlow as error:
                self.add_messages(context, *error.messages)

            if self.messages[-1].get("role") == "exit":
                break

        extra = self.messages[-1].get("extra")
        return extra if isinstance(extra, dict) else {}

    def step(self, context: ProjectRuntimeContext) -> list[Message]:
        """Query the model and execute its actions."""
        return self.execute_actions(context, self.query(context))

    def query(self, context: ProjectRuntimeContext) -> Message:
        """Query the model and append its native response."""
        if self.n_calls >= self.config.step_limit:
            raise StepLimitExceeded(
                {
                    "role": "exit",
                    "content": "StepLimitExceeded",
                    "extra": {
                        "exit_status": "StepLimitExceeded",
                        "submission": "",
                    },
                }
            )
        self.n_calls += 1
        message = self.model.query(self.messages)
        self.add_messages(context, message)
        return message

    def execute_actions(
        self,
        context: ProjectRuntimeContext,
        message: Message,
    ) -> list[Message]:
        """Execute actions and append their provider-formatted observations."""
        extra = message.get("extra")
        raw_actions = extra.get("actions", []) if isinstance(extra, dict) else []
        actions = [action for action in raw_actions if isinstance(action, dict)]
        outputs = [self.execute_tool(context, action) for action in actions]
        return self.add_messages(
            context,
            *self.model.format_observation_messages(message, outputs)
        )

    def add_messages(
        self,
        context: ProjectRuntimeContext,
        *messages: Message,
    ) -> list[Message]:
        appended = tuple(dict(message) for message in messages)
        self.append_persisted_messages(context, appended)
        self.messages.extend(appended)
        return list(appended)
