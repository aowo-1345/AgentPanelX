"""Private-facing ports used by the External Agent Runtime."""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel

from agentplanex.infrastructure.codex import CodexTurnResult
from agentplanex.services.external_agent_runtime.models import PreparedAgentTurn


class AgentOperation[InputT: BaseModel, OutputT](Protocol):
    """Role-owned static Contract and activation preparation."""

    operation_key: str
    output_schema: dict[str, Any]

    def contract_fingerprint(self) -> object: ...

    def request_fingerprint(self, payload: InputT) -> object: ...

    def prepare(
        self,
        payload: InputT,
        context: Any,
    ) -> PreparedAgentTurn: ...

    def validate(
        self,
        payload: InputT,
        context: Any,
        turn: CodexTurnResult,
    ) -> OutputT: ...

    def dump_result(self, output: OutputT) -> dict[str, Any]: ...

    def load_result(self, payload: dict[str, Any], context: Any) -> OutputT: ...
