"""Observable Project Owner configuration and project-binding behavior."""

from collections.abc import Callable
from pathlib import Path
from typing import ClassVar

import pytest

from agentplanex import cli
from agentplanex.domains import (
    ActionOutput,
    AgentExit,
    AgentExitStatus,
    Message,
    ProjectRuntimeContext,
)
from agentplanex.project_owner_agent.exception import ReplyToHuman
from agentplanex.project_runtime import ProjectRuntime
from agentplanex.project_runtime.executions import create_project_executions
from agentplanex.services import project_owner as project_owner_service
from agentplanex.services.owner_activation import ActivationDriveResult
from agentplanex.settings import (
    BashSettings,
    ModelSettings,
    ProjectOwnerAgentSettings,
    RuntimeSettings,
    Settings,
    load_settings,
)


class _ReplyingModel:
    constructions = 0
    queries: ClassVar[list[list[Message]]] = []

    def __init__(self, **_kwargs: object) -> None:
        type(self).constructions += 1

    def query(self, messages: list[Message]) -> Message:
        type(self).queries.append([dict(message) for message in messages])
        task = str(messages[-1].get("content", ""))
        raise ReplyToHuman(
            content=task,
            response={"role": "assistant", "content": task},
        )

    def format_observation_messages(
        self,
        message: Message,
        outputs: list[ActionOutput],
    ) -> list[Message]:
        raise AssertionError("The replying model does not call tools")


class _BashCallingModel:
    def __init__(self, **_kwargs: object) -> None:
        pass

    def query(self, messages: list[Message]) -> Message:
        latest = messages[-1]
        if latest.get("role") == "user":
            return {
                "role": "assistant",
                "content": "",
                "extra": {
                    "actions": [
                        {
                            "tool": "bash",
                            "call_id": "bash-test",
                            "arguments": {"command": latest["content"]},
                        }
                    ]
                },
            }

        output = latest["extra"]
        assert isinstance(output, dict)
        content = f"{output['output']}\n{output['exception_info']}".strip()
        raise ReplyToHuman(
            content=content,
            response={"role": "assistant", "content": content},
        )

    def format_observation_messages(
        self,
        message: Message,
        outputs: list[ActionOutput],
    ) -> list[Message]:
        return [{"role": "tool", "content": "bash result", "extra": outputs[0]}]


def _settings(
    *,
    bash_timeout_seconds: float = 30.0,
    bash_output_limit: int = 65_536,
) -> Settings:
    return Settings(
        project_owner_agent=ProjectOwnerAgentSettings(
            model=ModelSettings(name="test-model"),
        ),
        runtime=RuntimeSettings(
            bash=BashSettings(
                timeout_seconds=bash_timeout_seconds,
                output_limit=bash_output_limit,
            )
        ),
    )


def test_settings_load_model_agent_and_bash_configuration(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.yaml"
    settings_path.write_text(
        """
project_owner_agent:
  model:
    name: configured-model
    base_url: https://example.test/v1
    timeout_seconds: 12.5
  step_limit: 7
  max_consecutive_format_errors: 2
runtime:
  bash:
    timeout_seconds: 3.5
    output_limit: 4096
""".lstrip(),
        encoding="utf-8",
    )

    settings = load_settings(settings_path)

    assert settings.project_owner_agent.model.name == "configured-model"
    assert settings.project_owner_agent.model.base_url == "https://example.test/v1"
    assert settings.project_owner_agent.model.timeout_seconds == 12.5
    assert settings.project_owner_agent.step_limit == 7
    assert settings.project_owner_agent.max_consecutive_format_errors == 2
    assert settings.runtime.bash.timeout_seconds == 3.5
    assert settings.runtime.bash.output_limit == 4096


def test_unknown_settings_are_rejected(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.yaml"
    settings_path.write_text(
        """
project_owner_agent:
  model:
    name: configured-model
runtime:
  bash:
    timeout_seconds: 30
    output_limit: 65536
  unknown: true
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Failed to load AgentPlaneX settings"):
        load_settings(settings_path)


def test_project_executions_expose_and_dispatch_bash(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    executions = create_project_executions(
        project_path,
        RuntimeSettings(bash=BashSettings()),
    )

    result = executions.execute(
        ProjectRuntimeContext("test-runtime"),
        {"tool": "bash", "arguments": {"command": "pwd"}},
    )

    assert [tool.name for tool in executions.tools.tools] == [
        "bash",
        "request_plan_approval",
    ]
    assert result.output["returncode"] == 0
    assert result.output["output"].strip() == str(project_path.resolve())


def test_cli_only_passes_explicit_runtime_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class _Runtime:
        def submit_message(self, task: str) -> None:
            self.task = task

        def drive_next_activation(self) -> ActivationDriveResult:
            return ActivationDriveResult(
                activation=None,
                exit=AgentExit(
                    status=AgentExitStatus.REPLY_TO_HUMAN,
                    content=self.task,
                ),
            )

    def create_runtime(**kwargs: object) -> _Runtime:
        captured.update(kwargs)
        return _Runtime()

    monkeypatch.setattr(cli, "create_project_runtime", create_runtime)

    assert (
        cli.main(
            ["--cwd", str(tmp_path), "--mode", "yolo", "--print", "hello"]
        )
        == 0
    )
    assert captured == {
        "project_path": tmp_path,
        "approval_mode": "yolo",
    }


def test_cli_reports_missing_model_credentials(
    initialize_git_project: Callable[[], Path],
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    project_path = initialize_git_project()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_ADMIN_KEY", raising=False)

    result = cli.main(
        ["--cwd", str(project_path), "--mode", "yolo", "--print", "hello"]
    )

    assert result == 1
    assert "Missing credentials" in capfd.readouterr().err


def test_runtime_restores_owner_history_across_activations(
    initialize_git_project: Callable[[], Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ReplyingModel.constructions = 0
    _ReplyingModel.queries = []
    monkeypatch.setattr(project_owner_service, "JBBModel", _ReplyingModel)
    project_path = initialize_git_project()
    runtime = ProjectRuntime(
        project_path=project_path,
        settings=_settings(),
        approval_mode="yolo",
    )

    runtime.submit_message("first")
    first_result = runtime.drive_next_activation()
    runtime.submit_message("second")
    second_result = runtime.drive_next_activation()
    restarted_runtime = ProjectRuntime(
        project_path=project_path,
        settings=_settings(),
        approval_mode="yolo",
    )
    restarted_runtime.submit_message("third")
    third_result = restarted_runtime.drive_next_activation()

    first = first_result.exit
    second = second_result.exit
    third = third_result.exit
    assert first is not None
    assert second is not None
    assert third is not None

    assert first.content == "first"
    assert second.content == "second"
    assert third.content == "third"
    assert _ReplyingModel.constructions == 3
    restored_contents = [
        message.get("content") for message in _ReplyingModel.queries[-1]
    ]
    assert restored_contents[-5:] == [
        "first",
        "first",
        "second",
        "second",
        "third",
    ]


@pytest.mark.parametrize(
    ("command", "timeout_seconds", "output_limit", "expected"),
    [
        ("printf '%0200d' 0", 30.0, 64, "output truncated to 64 characters"),
        ("sleep 1", 0.01, 65_536, "Bash command timed out after 0.01s"),
    ],
)
def test_runtime_applies_bash_limits(
    initialize_git_project: Callable[[], Path],
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    timeout_seconds: float,
    output_limit: int,
    expected: str,
) -> None:
    monkeypatch.setattr(project_owner_service, "JBBModel", _BashCallingModel)
    project_path = initialize_git_project()
    runtime = ProjectRuntime(
        project_path=project_path,
        settings=_settings(
            bash_timeout_seconds=timeout_seconds,
            bash_output_limit=output_limit,
        ),
        approval_mode="yolo",
    )

    runtime.submit_message(command)
    result = runtime.drive_next_activation().exit
    assert result is not None

    assert expected in result.content
