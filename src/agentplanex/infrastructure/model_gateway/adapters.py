"""Provider-specific adapters for Responses-compatible endpoints."""

import os
from collections.abc import Mapping
from threading import Lock
from typing import Literal, cast

from openai import OpenAI, OpenAIError
from openai.types.responses import FunctionToolParam, ResponseInputParam
from openai.types.responses.response_create_params import (
    ResponseCreateParamsNonStreaming,
)
from openai.types.shared_params import Reasoning

from agentplanex.project_owner_agent.exception import ModelGatewayError
from agentplanex.project_owner_agent.models.responses import ResponsesRequest

type ReasoningEffort = Literal[
    "none", "minimal", "low", "medium", "high", "xhigh", "max"
]
type ServiceTier = Literal["auto", "default", "flex", "scale", "priority"]


class _OpenAICompatibleResponsesAdapter:
    """Shared SDK mechanics for one lazily-created Responses connection pool."""

    name: str
    reports_cache_usage: bool
    accepts_cache_affinity: bool

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
        params: ResponseCreateParamsNonStreaming = {
            "model": request.model,
            "instructions": request.instructions,
            "input": cast(ResponseInputParam, list(request.input)),
            "store": False,
            "stream": False,
        }
        if self.reasoning_effort is not None:
            params["reasoning"] = cast(Reasoning, {"effort": self.reasoning_effort})
        if self.service_tier is not None:
            params["service_tier"] = self.service_tier
        if request.tools:
            params["tools"] = cast(list[FunctionToolParam], list(request.tools))
            params["tool_choice"] = request.tool_choice
            params["parallel_tool_calls"] = True
        cache_key = request.cache_affinity_key if self.accepts_cache_affinity else None
        if cache_key is not None:
            params["prompt_cache_key"] = cache_key
        try:
            return client.responses.create(**params)
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


class QwenResponsesAdapter(_OpenAICompatibleResponsesAdapter):
    """Qwen Responses without AgentPlaneX cache controls or metrics."""

    name = "qwen"
    reports_cache_usage = False
    accepts_cache_affinity = False


class OpenAIResponsesAdapter(_OpenAICompatibleResponsesAdapter):
    """Official or locally proxied OpenAI Responses with cache affinity."""

    name = "openai"
    reports_cache_usage = True
    accepts_cache_affinity = True
