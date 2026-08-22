"""Provider-specific adapters for Responses-compatible endpoints."""

import os
from collections.abc import Mapping
from threading import Lock
from typing import Literal, cast

from openai import Omit, OpenAI, OpenAIError, omit
from openai.types.responses import FunctionToolParam, ResponseInputParam
from openai.types.shared_params import Reasoning

from agentplanex.project_owner_agent.exception import ModelGatewayError
from agentplanex.project_owner_agent.models.responses import ResponsesRequest

type ReasoningEffort = Literal[
    "none", "minimal", "low", "medium", "high", "xhigh", "max"
]
type ServiceTier = Literal["auto", "default", "flex", "scale", "priority"]


class QwenResponsesAdapter:
    """Send Qwen requests through one lazily-created OpenAI SDK client."""

    name = "qwen"
    reports_cache_usage = False

    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float,
        api_key_env: str,
        http_headers: Mapping[str, str] | None = None,
        reasoning_effort: ReasoningEffort | None = None,
        service_tier: ServiceTier | None = "priority",
    ) -> None:
        self.base_url = base_url
        self.timeout_seconds = timeout_seconds
        self.api_key_env = api_key_env
        self.http_headers = dict(http_headers or {})
        self.reasoning_effort = reasoning_effort
        self.service_tier = service_tier
        self.client: OpenAI | None = None
        self._lock = Lock()

    def create(self, request: ResponsesRequest) -> object:
        client = self._client()
        reasoning: Reasoning | Omit = (
            {"effort": self.reasoning_effort}
            if self.reasoning_effort is not None
            else omit
        )
        service_tier = self.service_tier if self.service_tier is not None else omit
        try:
            if request.tools:
                return client.responses.create(
                    model=request.model,
                    instructions=request.instructions,
                    input=cast(ResponseInputParam, list(request.input)),
                    tools=cast(list[FunctionToolParam], list(request.tools)),
                    store=False,
                    stream=False,
                    reasoning=reasoning,
                    service_tier=service_tier,
                    tool_choice=request.tool_choice,
                    parallel_tool_calls=True,
                )
            return client.responses.create(
                model=request.model,
                instructions=request.instructions,
                input=cast(ResponseInputParam, list(request.input)),
                store=False,
                stream=False,
                reasoning=reasoning,
                service_tier=service_tier,
            )
        except OpenAIError as error:
            raise ModelGatewayError(f"Responses gateway request failed: {error}") from error

    def close(self) -> None:
        """Close the shared SDK client when the application shuts down."""

        with self._lock:
            client = self.client
            self.client = None
        if client is not None:
            client.close()

    def _client(self) -> OpenAI:
        if self.client is None:
            with self._lock:
                if self.client is None:
                    api_key = os.getenv(self.api_key_env)
                    if api_key is None or not api_key.strip():
                        raise ModelGatewayError(
                            "Missing credentials: environment variable "
                            f"{self.api_key_env} is not set"
                        )
                    try:
                        self.client = OpenAI(
                            api_key=api_key,
                            base_url=self.base_url,
                            timeout=self.timeout_seconds,
                            max_retries=2,
                            default_headers=self.http_headers,
                        )
                    except OpenAIError as error:
                        raise ModelGatewayError(
                            f"Failed to initialize Responses gateway: {error}"
                        ) from error
        return self.client
