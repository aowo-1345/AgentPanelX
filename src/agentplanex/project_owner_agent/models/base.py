"""Model protocol used by the Agent loop."""

from typing import Protocol

from agentplanex.project_owner_agent.contracts import ActionOutput, Message


class Model(Protocol):
    def query(self, messages: list[Message]) -> Message: ...

    def format_observation_messages(
        self,
        message: Message,
        outputs: list[ActionOutput],
    ) -> list[Message]: ...
