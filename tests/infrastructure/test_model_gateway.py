"""Critical Model Gateway configuration and transport invariants."""

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import agentplanex.infrastructure.model_gateway.adapters as adapters_module
from agentplanex import bootstrap
from agentplanex.infrastructure.logging import configure_logging
from agentplanex.infrastructure.model_gateway import ModelGateway, OpenAIResponsesAdapter
from agentplanex.project_owner_agent.exception import ModelGatewayError
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


class _FailingAdapter(_RecordingAdapter):
    def __init__(self, error: ModelGatewayError) -> None:
        super().__init__(object())
        self.error = error

    def create(self, request: ResponsesRequest) -> object:
        self.requests.append(request)
        raise self.error


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


def test_unavailable_file_logging_cannot_replace_gateway_results_or_errors(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    unavailable = tmp_path / "not-a-directory"
    unavailable.write_text("blocks creation of a file sink below this path", encoding="utf-8")
    configure_logging(unavailable)

    response = object()
    assert ModelGateway(adapter=_RecordingAdapter(response)).create(_request()) is response

    expected = ModelGatewayError("normalized gateway failure")
    with pytest.raises(ModelGatewayError) as caught:
        ModelGateway(adapter=_FailingAdapter(expected)).create(_request())

    assert caught.value is expected
    assert "AgentPanelX file logging is unavailable" in capsys.readouterr().err


def test_openai_adapter_is_lazy_and_maps_cache_affinity_and_usage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clients: list[dict[str, object]] = []
    requests: list[dict[str, object]] = []
    provider_response = SimpleNamespace(
        usage=SimpleNamespace(
            input_tokens=4096,
            output_tokens=12,
            input_tokens_details=SimpleNamespace(cached_tokens=3072),
        ),
        output=[],
    )

    class _Responses:
        def create(self, **kwargs: object) -> object:
            requests.append(kwargs)
            return provider_response

    class _Client:
        responses = _Responses()

        def close(self) -> None:
            pass

    def create_client(**kwargs: object) -> _Client:
        clients.append(kwargs)
        return _Client()

    monkeypatch.setenv("CLIPROXY_API_KEY", "local-proxy-secret")
    monkeypatch.setattr(adapters_module, "OpenAI", create_client)
    adapter = OpenAIResponsesAdapter(
        base_url="http://127.0.0.1:8317/v1",
        timeout_seconds=60,
        api_key_env="CLIPROXY_API_KEY",
        service_tier=None,
    )
    assert clients == []
    configure_logging(tmp_path)

    gateway = ModelGateway(adapter=adapter)
    response = gateway.create(
        ResponsesRequest(
            model="gpt-5.6-luna",
            instructions="Reply briefly.",
            input=({"role": "user", "content": "hello"},),
            tools=(),
            tool_choice="none",
            cache_affinity_key="stable-affinity",
        )
    )

    assert response is provider_response
    assert len(clients) == 1
    assert requests[0]["prompt_cache_key"] == "stable-affinity"
    adapter.create(_request())
    assert "prompt_cache_key" not in requests[1]
    event = next(tmp_path.glob("agentplanex-*.log")).read_text(encoding="utf-8")
    assert "adapter=openai" in event
    assert "cached_tokens=3072" in event
    assert "stable-affinity" not in event
    assert "local-proxy-secret" not in event


def test_bootstrap_binds_the_explicit_openai_adapter_without_connecting() -> None:
    configured = load_settings(DEFAULT_SETTINGS_PATH)
    settings = configured.model_copy(
        update={
            "project_owner_agent": configured.project_owner_agent.model_copy(
                update={"active_model": "codex"}
            )
        }
    )

    gateway = bootstrap.create_responses_transport(settings)

    assert isinstance(gateway.adapter, OpenAIResponsesAdapter)
    assert gateway.adapter.timeout_seconds == 180.0
    assert gateway.adapter.max_retries == 1
    assert gateway.adapter.client is None


def test_bootstrap_uses_environment_before_the_local_cliproxy_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clients: list[dict[str, object]] = []

    class _Responses:
        @staticmethod
        def create(**_kwargs: object) -> object:
            return {"output": []}

    class _Client:
        responses = _Responses()

        def close(self) -> None:
            pass

    def create_client(**kwargs: object) -> _Client:
        clients.append(kwargs)
        return _Client()

    data_home = tmp_path / ".agentplanex"
    proxy_config = data_home / "secrets" / "cliproxy" / "config.yaml"
    proxy_config.parent.mkdir(parents=True)
    proxy_config.write_text(
        yaml.safe_dump({"api-keys": ["local-file-key"]}),
        encoding="utf-8",
    )
    configured = load_settings(DEFAULT_SETTINGS_PATH)
    settings = configured.model_copy(
        update={
            "project_owner_agent": configured.project_owner_agent.model_copy(
                update={"active_model": "codex"}
            ),
            "workspace": configured.workspace.model_copy(
                update={"data_home": data_home}
            ),
        }
    )
    monkeypatch.setattr(adapters_module, "OpenAI", create_client)

    monkeypatch.delenv("CLIPROXY_API_KEY", raising=False)
    file_gateway = bootstrap.create_responses_transport(settings)
    file_gateway.create(_request())

    monkeypatch.setenv("CLIPROXY_API_KEY", "environment-key")
    environment_gateway = bootstrap.create_responses_transport(settings)
    environment_gateway.create(_request())

    assert [client["api_key"] for client in clients] == [
        "local-file-key",
        "environment-key",
    ]
    assert [client["timeout"] for client in clients] == [180.0, 180.0]
    assert [client["max_retries"] for client in clients] == [1, 1]
