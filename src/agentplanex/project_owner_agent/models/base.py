"""Model protocol used by the Agent loop."""

from typing import Any, Protocol

from agentplanex.domains import ActionOutput

type Message = dict[str, Any]


class Model(Protocol):
    def query(self, messages: list[Message]) -> Message: ...

    def format_observation_messages(
        self,
        message: Message,
        outputs: list[ActionOutput],
    ) -> list[Message]: ...
