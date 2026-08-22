"""Critical Model Gateway configuration and transport invariants."""

from pathlib import Path

import pytest
import yaml

from agentplanex.infrastructure.logging import configure_logging
from agentplanex.infrastructure.model_gateway import ModelGateway
from agentplanex.project_owner_agent.models.responses import ResponsesRequest
from agentplanex.settings import DEFAULT_SETTINGS_PATH, load_settings


class _RecordingAdapter:
    name = "qwen"
    reports_cache_usage = False

    def __init__(self, response: object) -> None:
        self.response = response
        self.requests: list[ResponsesRequest] = []
        self.closed = 0

    def create(self, request: ResponsesRequest) -> object:
        self.requests.append(request)
        return self.response

    def close(self) -> None:
        self.closed += 1


def _request() -> ResponsesRequest:
    return ResponsesRequest(
        model="qwen-test",
        instructions="Reply briefly.",
        input=({"role": "user", "content": "hello"},),
        tools=(),
        tool_choice="none",
    )


def test_settings_require_a_known_model_adapter(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.yaml"
    raw = load_settings(DEFAULT_SETTINGS_PATH).model_dump(mode="json")
    raw["project_owner_agent"]["models"]["qwen"]["adapter"] = "unknown"
    settings_path.write_text(yaml.safe_dump(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="Failed to load AgentPlaneX settings"):
        load_settings(settings_path)


def test_gateway_returns_the_provider_response_and_records_one_safe_event(
    tmp_path: Path,
) -> None:
    configure_logging(tmp_path)
    response = {
        "usage": {"input_tokens": 21, "output_tokens": 5},
        "output": [],
    }
    adapter = _RecordingAdapter(response)
    gateway = ModelGateway(adapter=adapter)

    assert gateway.create(_request()) is response

    log_files = list(tmp_path.glob("agentplanex-*.log"))
    assert len(log_files) == 1
    event = log_files[0].read_text(encoding="utf-8")
    assert event.count("event=model_gateway_call") == 1
    assert "adapter=qwen" in event
    assert "model=qwen-test" in event
    assert "status=succeeded" in event
    assert "input_tokens=21" in event
    assert "output_tokens=5" in event
    assert "cached_tokens" not in event
    assert "hello" not in event


def test_gateway_close_is_idempotent() -> None:
    adapter = _RecordingAdapter(object())
    gateway = ModelGateway(adapter=adapter)

    gateway.close()
    gateway.close()

    assert adapter.closed == 1
