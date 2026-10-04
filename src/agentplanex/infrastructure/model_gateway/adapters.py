"""Provider-specific adapters for Responses-compatible endpoints."""

import os
from collections.abc import Iterable, Mapping
from threading import Lock
from typing import Literal, cast

from openai import OpenAI, OpenAIError
from openai.types.responses import FunctionToolParam, ResponseInputParam
from openai.types.responses.response_create_params import (
    ResponseCreateParamsNonStreaming,
    ResponseCreateParamsStreaming,
)
from openai.types.shared_params import Reasoning

from agentplanex.project_owner_agent.exception import ModelGatewayError
from agentplanex.project_owner_agent.models.responses import ResponseEvent, ResponsesRequest

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
        max_retries: int = 2,
        fallback_api_key: str | None = None,
        http_headers: Mapping[str, str] | None = None,
        reasoning_effort: ReasoningEffort | None = None,
        service_tier: ServiceTier | None = "priority",
    ) -> None:
        self.base_url = base_url
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.api_key_env = api_key_env
        self._fallback_api_key = fallback_api_key
        self.http_headers = dict(http_headers or {})
        self.reasoning_effort = reasoning_effort
        self.service_tier = service_tier
        self.client: OpenAI | None = None
        self._closed = False
        self._lock = Lock()

    def create(self, request: ResponsesRequest) -> object:
        client = self._client()
        params = cast(ResponseCreateParamsNonStreaming, self._params(request, stream=False))
        try:
            return client.responses.create(**params)
        except OpenAIError as error:
            raise ModelGatewayError(f"Responses gateway request failed: {error}") from error

    def stream(self, request: ResponsesRequest) -> Iterable[ResponseEvent]:
        """Yield normalized Responses events without retrying after stream start."""
        client = self._client()
        params = cast(ResponseCreateParamsStreaming, self._params(request, stream=True))
        try:
            calls: dict[str, tuple[str, str]] = {}
            with client.responses.create(**params) as response_stream:
                for event in response_stream:
                    event_type = _event_type(event)
                    if event_type == "response.output_text.delta":
                        yield ResponseEvent(
                            type="text_delta", delta=str(getattr(event, "delta", ""))
                        )
                    elif event_type == "response.output_item.added":
                        item = getattr(event, "item", None)
                        if getattr(item, "type", None) == "function_call" and item is not None:
                            item_id = str(getattr(item, "id", ""))
                            call_id = str(getattr(item, "call_id", item_id))
                            name = str(getattr(item, "name", "tool"))
                            calls[item_id] = (call_id, name)
                            yield ResponseEvent(
                                type="tool_delta", call_id=call_id, name=name,
                                delta=str(getattr(item, "arguments", "")),
                            )
                    elif event_type == "response.function_call_arguments.delta":
                        item_id = str(getattr(event, "item_id", ""))
                        call_id, name = calls.get(item_id, (item_id, "tool"))
                        yield ResponseEvent(
                            type="tool_delta", delta=str(getattr(event, "delta", "")),
                            call_id=call_id, name=name,
                        )
                    elif event_type == "response.completed":
                        yield ResponseEvent(
                            type="completed", response=getattr(event, "response", None)
                        )
                        return
                    elif event_type in {"response.failed", "response.incomplete", "error"}:
                        raise ModelGatewayError(
                            f"Responses gateway request failed: {_event_error(event)}"
                        )
            raise ModelGatewayError("Responses stream ended without completion")
        except OpenAIError as error:
            raise ModelGatewayError(f"Responses gateway request failed: {error}") from error

    def _params(self, request: ResponsesRequest, *, stream: bool) -> dict[str, object]:
        params: dict[str, object] = {
            "model": request.model,
            "instructions": request.instructions,
            "input": cast(ResponseInputParam, list(request.input)),
            "store": False,
            "stream": stream,
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
        return params

    def close(self) -> None:
        """Close the shared SDK client when the application shuts down."""

        with self._lock:
            self._closed = True
            client = self.client
            self.client = None
        if client is not None:
            client.close()

    def _client(self) -> OpenAI:
        with self._lock:
            if self._closed:
                raise ModelGatewayError("Responses gateway is closed")
            if self.client is None:
                api_key = os.getenv(self.api_key_env)
                if api_key is None or not api_key.strip():
                    api_key = self._fallback_api_key
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
                        max_retries=self.max_retries,
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


def _event_type(event: object) -> str:
    return (
        str(event.get("type", ""))
        if isinstance(event, dict)
        else str(getattr(event, "type", ""))
    )


def _event_error(event: object) -> str:
    response = getattr(event, "response", None)
    error = getattr(event, "error", None) or getattr(response, "error", None)
    if isinstance(error, dict):
        return str(error.get("message") or error)
    return str(
        getattr(error, "message", error)
        or getattr(event, "message", None)
        or getattr(response, "incomplete_details", None)
        or "Responses stream failed"
    )
