"""Exit-status contract for the credentialed streaming smoke CLI."""

from typing import Any

import pytest

import test_stream_request as stream_cli
from agentplanex.settings import ModelSettings


class _Client:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize(("succeeded", "expected_code"), [(True, 0), (False, 1)])
def test_once_exit_status_reflects_the_stream_result(
    monkeypatch: pytest.MonkeyPatch,
    succeeded: bool,
    expected_code: int,
) -> None:
    client = _Client()
    model = ModelSettings(name="test-model", base_url="https://example.test/v1")
    observed: dict[str, object] = {}
    monkeypatch.setattr(stream_cli, "_create_client", lambda: (client, model))

    def run_turn(
        received_client: Any,
        received_model: ModelSettings,
        history: stream_cli.History,
        prompt: str,
    ) -> bool:
        observed.update(
            client=received_client,
            model=received_model,
            history=history,
            prompt=prompt,
        )
        return succeeded

    monkeypatch.setattr(stream_cli, "_run_turn", run_turn)

    assert stream_cli.main(["--once", "only", "reply", "ok"]) == expected_code
    assert observed == {
        "client": client,
        "model": model,
        "history": [],
        "prompt": "only reply ok",
    }
    assert client.closed is True


def test_once_requires_a_prompt_before_loading_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_client() -> tuple[Any, ModelSettings]:
        raise AssertionError("credentials must not be loaded without a prompt")

    monkeypatch.setattr(stream_cli, "_create_client", unexpected_client)

    assert stream_cli.main(["--once"]) == 2
