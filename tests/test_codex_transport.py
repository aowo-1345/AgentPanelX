"""Codex transport permission boundaries."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from openai_codex import ApprovalMode, Sandbox

import agentplanex.infrastructure.codex as codex_module
from agentplanex.infrastructure.codex import CodexTurnRequest, CodexTurnTransport


@pytest.mark.parametrize(
    ("network_access", "expected_override"),
    [
        (True, "sandbox_workspace_write.network_access=true"),
        (False, "sandbox_workspace_write.network_access=false"),
    ],
)
def test_transport_keeps_workspace_write_and_sets_codex_network_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    network_access: bool,
    expected_override: str,
) -> None:
    captured: dict[str, object] = {}

    class _Turn:
        def run(self) -> object:
            return SimpleNamespace(
                id="turn-1",
                status=SimpleNamespace(value="completed"),
                final_response='{"summary":"done"}',
            )

        def interrupt(self) -> None:
            raise AssertionError("Completed turn must not be interrupted")

    class _Thread:
        id = "thread-1"

        def turn(self, input_items: object, **kwargs: object) -> _Turn:
            captured["input_items"] = input_items
            captured["turn"] = kwargs
            return _Turn()

    class _Codex:
        def __init__(self, config: object) -> None:
            captured["config"] = config

        def thread_start(self, **kwargs: object) -> _Thread:
            captured["thread_start"] = kwargs
            return _Thread()

        def close(self) -> None:
            captured["closed"] = True

    monkeypatch.setattr(codex_module, "Codex", _Codex)
    transport = CodexTurnTransport(
        executable=None,
        model=None,
        timeout_seconds=5,
        response_limit=1_024,
        network_access=network_access,
    )

    result = transport.run(
        CodexTurnRequest(
            thread_id=None,
            workspace=tmp_path,
            developer_instructions="Implement the task.",
            message="Run the required command.",
            mentions=(),
            skills=(("observe", tmp_path / "SKILL.md"),),
        )
    )

    config = captured["config"]
    assert isinstance(config, codex_module.CodexConfig)
    assert config.config_overrides == (expected_override,)
    thread_start = captured["thread_start"]
    assert isinstance(thread_start, dict)
    assert thread_start["sandbox"] is Sandbox.workspace_write
    assert thread_start["approval_mode"] is ApprovalMode.deny_all
    turn = captured["turn"]
    assert isinstance(turn, dict)
    assert "sandbox" not in turn
    assert turn["approval_mode"] is ApprovalMode.deny_all
    input_items = captured["input_items"]
    assert isinstance(input_items, list)
    assert isinstance(input_items[0], codex_module.TextInput)
    assert isinstance(input_items[1], codex_module.SkillInput)
    assert captured["closed"] is True
    assert result.thread_id == "thread-1"
