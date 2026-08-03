"""Observable tests for the direct Tool Action debug entry point."""

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from agentplanex.services import project_runtime as project_runtime_service
from scripts import debug_tool_cli


def test_executes_project_bound_action_without_constructing_a_model(
    initialize_git_project: Callable[[], Path],
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    project_path = initialize_git_project()

    class _UnexpectedModel:
        def __init__(self, **_kwargs: object) -> None:
            raise AssertionError("tool debug entry must not construct a model")

    monkeypatch.setattr(project_runtime_service, "JBBModel", _UnexpectedModel)

    result = debug_tool_cli.main(
        [
            "--cwd",
            str(project_path),
            "--print",
            json.dumps(
                {
                    "tool": "bash",
                    "call_id": "project-bound-pwd",
                    "arguments": {"command": "pwd"},
                }
            ),
        ]
    )

    captured = capfd.readouterr()
    response = json.loads(captured.out)
    assert result == 0
    assert captured.err == ""
    assert response == {
        "call_id": "project-bound-pwd",
        "tool": "bash",
        "ok": True,
        "result": {
            "output": f"{project_path.resolve()}\n",
            "returncode": 0,
            "exception_info": "",
        },
        "exit": None,
    }
    assert (project_path / ".agentplanex" / "agentplanex.sqlite3").is_file()


def test_returns_unknown_tool_failure_as_json(
    initialize_git_project: Callable[[], Path],
    capfd: pytest.CaptureFixture[str],
) -> None:
    project_path = initialize_git_project()

    result = debug_tool_cli.main(
        [
            "--cwd",
            str(project_path),
            "--print",
            '{"tool":"missing","arguments":{}}',
        ]
    )

    response = json.loads(capfd.readouterr().out)
    assert result == 1
    assert response["tool"] == "missing"
    assert response["ok"] is False
    assert response["result"]["returncode"] == -1
    assert response["result"]["exception_info"] == "Unknown tool: 'missing'"


def test_invalid_json_does_not_create_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    def unexpected_runtime(**_kwargs: object) -> None:
        raise AssertionError("invalid input must not create a Runtime")

    monkeypatch.setattr(debug_tool_cli, "create_project_runtime", unexpected_runtime)

    result = debug_tool_cli.main(
        ["--cwd", str(tmp_path), "--print", "not-json"]
    )

    response = json.loads(capfd.readouterr().out)
    assert result == 2
    assert response["ok"] is False
    assert response["result"] is None
    assert "Tool action must be a JSON object" in response["error"]
