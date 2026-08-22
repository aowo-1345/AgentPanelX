"""Observable transport boundary for logical model calls."""

from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from threading import Lock
from time import monotonic
from typing import Protocol

from loguru import logger

from agentplanex.project_owner_agent.models.responses import ResponsesRequest


class ModelGatewayAdapter(Protocol):
    """Provider adapter owned and closed by one application Gateway."""

    name: str
    reports_cache_usage: bool

    def create(self, request: ResponsesRequest) -> object: ...

    def close(self) -> None: ...


@dataclass(slots=True)
class ModelGateway:
    """Record one event around each logical Responses call."""

    adapter: ModelGatewayAdapter
    _closed: bool = field(default=False, init=False, repr=False)
    _close_lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def create(self, request: ResponsesRequest) -> object:
        started = monotonic()
        try:
            response = self.adapter.create(request)
        except BaseException:
            self._record_call(
                request=request,
                status="failed",
                duration_ms=_duration_ms(started),
                response=None,
            )
            raise
        self._record_call(
            request=request,
            status="succeeded",
            duration_ms=_duration_ms(started),
            response=response,
        )
        return response

    def close(self) -> None:
        """Release the shared Adapter exactly once."""

        with self._close_lock:
            if self._closed:
                return
            self._closed = True
        self.adapter.close()

    def _record_call(
        self,
        *,
        request: ResponsesRequest,
        status: str,
        duration_ms: int,
        response: object | None,
    ) -> None:
        usage = _value(response, "usage")
        fields: list[tuple[str, object | None]] = [
            ("event", "model_gateway_call"),
            ("adapter", self.adapter.name),
            ("model", request.model),
            ("status", status),
            ("duration_ms", duration_ms),
            ("input_tokens", _value(usage, "input_tokens")),
            ("output_tokens", _value(usage, "output_tokens")),
        ]
        if self.adapter.reports_cache_usage:
            details = _value(usage, "input_tokens_details")
            cached_tokens = _value(details, "cached_tokens")
            if cached_tokens is not None:
                fields.append(("cached_tokens", cached_tokens))
            cache_write_tokens = _cache_write_tokens(usage, details)
            if cache_write_tokens is not None:
                fields.append(("cache_write_tokens", cache_write_tokens))
        message = " ".join(f"{key}={_safe_value(value)}" for key, value in fields)
        with suppress(Exception):
            logger.info(message)


def _duration_ms(started: float) -> int:
    return max(0, round((monotonic() - started) * 1000))


def _value(source: object | None, name: str) -> object | None:
    if isinstance(source, Mapping):
        return source.get(name)
    return getattr(source, name, None)


def _cache_write_tokens(
    usage: object | None,
    details: object | None,
) -> object | None:
    for name in ("cache_write_tokens", "cache_creation_tokens"):
        value = _value(details, name)
        if value is not None:
            return value
        value = _value(usage, name)
        if value is not None:
            return value
    return None


def _safe_value(value: object | None) -> str:
    if value is None:
        return "-"
    return str(value).replace("\\", "\\\\").replace(" ", "\\ ").replace("\n", "\\n")
