"""Observable Project Owner configuration and project-binding behavior."""

import socket
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import ClassVar, cast

import httpx
import pytest
import yaml
from openai import APIConnectionError, omit

from agentplanex import bootstrap, cli
from agentplanex.domains import (
    ActionOutput,
    AgentExit,
    AgentExitStatus,
    Message,
    ProjectRuntimeState,
    SummaryHistory,
)
from agentplanex.infrastructure import local_shell as local_shell_module
from agentplanex.infrastructure import openai_responses as openai_responses_module
from agentplanex.infrastructure.openai_responses import OpenAIResponsesTransport
from agentplanex.infrastructure.sqlite import SQLiteDatabase
from agentplanex.infrastructure.sqlite.repositories import (
    SQLiteMessageHistoryRepository,
    SQLiteProjectOwnerAgentRepository,
    SQLiteSummaryHistoryRepository,
)
from agentplanex.project_owner_agent.exception import ModelGatewayError, ReplyToHuman
from agentplanex.project_owner_agent.models.responses import (
    ResponsesRequest,
)
from agentplanex.project_runtime import ProjectRuntime
from agentplanex.services.owner_activation import ActivationDriveResult
from agentplanex.services.project_runtime_context import _owner as project_owner_service
from agentplanex.settings import (
    DEFAULT_SETTINGS_PATH,
    DEFAULT_WORKSPACE_DATA_HOME,
    BashSettings,
    ModelSettings,
    ProjectOwnerAgentSettings,
    RuntimeSettings,
    Settings,
    load_settings,
)
from tests.runtime_support import compose_test_executions


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


class _UnusedResponsesTransport:
    def create(self, _request: ResponsesRequest) -> object:
        raise AssertionError("This test replaces the Project Owner model")


_UNUSED_RESPONSES_TRANSPORT = _UnusedResponsesTransport()


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


class _PolicyAwareBashModel:
    calls = 0
    observation: ClassVar[ActionOutput | None] = None

    def __init__(self, **_kwargs: object) -> None:
        pass

    def query(self, messages: list[Message]) -> Message:
        type(self).calls += 1
        latest = messages[-1]
        if latest.get("role") == "user":
            return {
                "role": "assistant",
                "content": "",
                "extra": {
                    "actions": [
                        {
                            "tool": "bash",
                            "call_id": "bash-policy-test",
                            "arguments": {"command": latest["content"]},
                        }
                    ]
                },
            }

        output = latest.get("extra")
        assert isinstance(output, dict)
        type(self).observation = output
        assert output["error_type"] == "SANDBOX_POLICY_DENIED"
        assert output["blocked_capability"] == "network"
        raise ReplyToHuman(
            content="Network access is blocked; user action is required.",
            response={
                "role": "assistant",
                "content": "Network access is blocked; user action is required.",
            },
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
    configured = load_settings(DEFAULT_SETTINGS_PATH)
    return configured.model_copy(
        update={
            "project_owner_agent": ProjectOwnerAgentSettings(
                active_model="test",
                models={"test": ModelSettings(name="test-model")},
            ),
            "runtime": configured.runtime.model_copy(
                update={
                    "bash": BashSettings(
                        timeout_seconds=bash_timeout_seconds,
                        output_limit=bash_output_limit,
                    )
                }
            ),
        }
    )


def test_settings_load_model_agent_and_bash_configuration(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.yaml"
    raw = load_settings(DEFAULT_SETTINGS_PATH).model_dump(mode="json")
    raw["project_owner_agent"] = {
        "active_model": "configured",
        "models": {
            "configured": {
                "name": "configured-model",
                "base_url": "https://example.test/v1",
                "api_key_env": "EXAMPLE_API_KEY",
                "http_headers": {"x-example": "configured"},
                "reasoning_effort": "high",
                "service_tier": None,
                "timeout_seconds": 12.5,
            }
        },
        "step_limit": 7,
        "max_consecutive_format_errors": 2,
    }
    raw["runtime"]["bash"] = {"timeout_seconds": 3.5, "output_limit": 4096}
    settings_path.write_text(yaml.safe_dump(raw), encoding="utf-8")

    settings = load_settings(settings_path)

    model = settings.project_owner_agent.selected_model
    assert settings.project_owner_agent.active_model == "configured"
    assert model.name == "configured-model"
    assert model.base_url == "https://example.test/v1"
    assert model.api_key_env == "EXAMPLE_API_KEY"
    assert model.http_headers == {
        "x-example": "configured"
    }
    assert model.reasoning_effort == "high"
    assert model.service_tier is None
    assert model.timeout_seconds == 12.5
    assert settings.project_owner_agent.step_limit == 7
    assert settings.project_owner_agent.max_consecutive_format_errors == 2
    assert settings.runtime.bash.timeout_seconds == 3.5
    assert settings.runtime.bash.output_limit == 4096
    assert settings.runtime.codex.network_access is True


def test_responses_transport_applies_selected_gateway_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    response = object()

    class _Responses:
        def create(self, **kwargs: object) -> object:
            captured["request"] = kwargs
            return response

    class _Client:
        responses = _Responses()

    def create_client(**kwargs: object) -> _Client:
        captured["client"] = kwargs
        return _Client()

    monkeypatch.setenv("TOOLCODE_API_KEY", "test-secret")
    monkeypatch.setattr(openai_responses_module, "OpenAI", create_client)
    transport = OpenAIResponsesTransport(
        base_url="https://toolcode.example",
        timeout_seconds=240.0,
        api_key_env="TOOLCODE_API_KEY",
        http_headers={"x-openai-actor-authorization": "local-image-extension"},
        reasoning_effort="high",
        service_tier=None,
    )

    result = transport.create(
        ResponsesRequest(
            model="gpt-5.6-sol",
            instructions="Only reply with ok.",
            input=({"role": "user", "content": "hello"},),
            tools=(),
            tool_choice="none",
        )
    )

    assert result is response
    assert captured["client"] == {
        "api_key": "test-secret",
        "base_url": "https://toolcode.example",
        "timeout": 240.0,
        "max_retries": 2,
        "default_headers": {
            "x-openai-actor-authorization": "local-image-extension"
        },
    }
    request = captured["request"]
    assert isinstance(request, dict)
    assert request["model"] == "gpt-5.6-sol"
    assert request["reasoning"] == {"effort": "high"}
    assert request["service_tier"] is omit


def test_responses_transport_exposes_final_sdk_failure_as_gateway_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk_error = APIConnectionError(
        request=httpx.Request("POST", "https://toolcode.example/responses")
    )

    class _Responses:
        def create(self, **_kwargs: object) -> object:
            raise sdk_error

    class _Client:
        responses = _Responses()

    monkeypatch.setenv("TOOLCODE_API_KEY", "test-secret")
    monkeypatch.setattr(
        openai_responses_module,
        "OpenAI",
        lambda **_kwargs: _Client(),
    )
    transport = OpenAIResponsesTransport(
        base_url="https://toolcode.example",
        timeout_seconds=60.0,
        api_key_env="TOOLCODE_API_KEY",
    )

    with pytest.raises(ModelGatewayError) as raised:
        transport.create(
            ResponsesRequest(
                model="gpt-5.6-sol",
                instructions="Only reply with ok.",
                input=({"role": "user", "content": "hello"},),
                tools=(),
                tool_choice="none",
            )
        )

    assert raised.value.__cause__ is sdk_error


def test_workspace_runtimes_share_one_responses_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transports: list[object] = []
    closed: list[object] = []

    class _Transport:
        def close(self) -> None:
            closed.append(self)

    transport = _Transport()

    def create_transport(**_kwargs: object) -> _Transport:
        return transport

    def create_runtime(**kwargs: object) -> ProjectRuntime:
        transports.append(kwargs["responses_transport"])
        return cast(ProjectRuntime, object())

    monkeypatch.setattr(bootstrap, "OpenAIResponsesTransport", create_transport)
    monkeypatch.setattr(bootstrap, "create_project_runtime", create_runtime)
    configured = load_settings(DEFAULT_SETTINGS_PATH)
    settings = configured.model_copy(
        update={
            "workspace": configured.workspace.model_copy(
                update={"data_home": tmp_path}
            )
        }
    )

    workspace = bootstrap.create_workspace(settings)
    workspace.runtime_factory(tmp_path / "feature-a")
    workspace.runtime_factory(tmp_path / "feature-b")
    workspace.close()

    assert transports == [transport, transport]
    assert closed == [transport]


def test_repository_settings_select_a_portable_agentplanex_data_home() -> None:
    settings = load_settings(DEFAULT_SETTINGS_PATH)

    assert settings.workspace.data_home == DEFAULT_WORKSPACE_DATA_HOME
    assert settings.workspace.data_home == Path(".agentplanex")
    assert settings.workspace.max_parallel_features == 4


def test_repository_settings_select_a_declared_model_without_embedded_credentials(
) -> None:
    owner = load_settings(DEFAULT_SETTINGS_PATH).project_owner_agent
    model = owner.selected_model

    assert owner.active_model in owner.models
    assert model.name.strip()
    assert model.base_url.startswith("https://")
    assert model.api_key_env.endswith("_API_KEY")
    assert "api_key" not in model.model_dump()


def test_settings_can_select_an_alternate_declared_gateway(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.yaml"
    raw = load_settings(DEFAULT_SETTINGS_PATH).model_dump(mode="json")
    owner_raw = raw["project_owner_agent"]
    alternates = [
        alias
        for alias in owner_raw["models"]
        if alias != owner_raw["active_model"]
    ]
    if not alternates:
        pytest.skip("Repository configuration declares one model gateway")
    alternate = alternates[0]
    owner_raw["active_model"] = alternate
    settings_path.write_text(yaml.safe_dump(raw), encoding="utf-8")

    owner = load_settings(settings_path).project_owner_agent

    assert owner.active_model == alternate
    assert owner.selected_model is owner.models[alternate]


def test_unknown_active_model_is_rejected(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.yaml"
    raw = load_settings(DEFAULT_SETTINGS_PATH).model_dump(mode="json")
    raw["project_owner_agent"]["active_model"] = "missing"
    settings_path.write_text(yaml.safe_dump(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="Failed to load AgentPlaneX settings"):
        load_settings(settings_path)


def test_legacy_single_model_configuration_is_rejected(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.yaml"
    raw = load_settings(DEFAULT_SETTINGS_PATH).model_dump(mode="json")
    owner = raw["project_owner_agent"]
    owner["model"] = owner["models"][owner["active_model"]]
    del owner["active_model"]
    del owner["models"]
    settings_path.write_text(yaml.safe_dump(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="Failed to load AgentPlaneX settings"):
        load_settings(settings_path)


def test_unknown_settings_are_rejected(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.yaml"
    raw = load_settings(DEFAULT_SETTINGS_PATH).model_dump(mode="json")
    raw["runtime"]["unknown"] = True
    settings_path.write_text(yaml.safe_dump(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="Failed to load AgentPlaneX settings"):
        load_settings(settings_path)


@pytest.mark.parametrize("invalid", ["missing", "blank"])
def test_incomplete_prompt_catalog_is_rejected(
    tmp_path: Path,
    invalid: str,
) -> None:
    settings_path = tmp_path / "settings.yaml"
    raw = load_settings(DEFAULT_SETTINGS_PATH).model_dump(mode="json")
    prompts = raw["runtime"]["prompts"]
    if invalid == "missing":
        del prompts["stage_executor"]
    else:
        prompts["planner"]["role"] = "   "
    settings_path.write_text(yaml.safe_dump(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="Failed to load AgentPlaneX settings"):
        load_settings(settings_path)


def test_project_executions_expose_and_dispatch_bash(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    composed = compose_test_executions(
        project_path,
        _settings().runtime,
    )

    result = composed.executions.execute(
        composed.state,
        {"tool": "bash", "arguments": {"command": "pwd"}},
    )

    assert [tool.name for tool in composed.executions.tools.tools] == [
        "bash",
        "request_plan_approval",
        "talk_to_agent",
        "update_milestones",
        "run_next_milestone",
        "decide_milestone_candidate",
    ]
    assert result.output["returncode"] == 0
    assert result.output["output"].strip() == str(project_path.resolve())


def test_talk_tool_renders_configured_agent_cards_with_stable_schema(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    settings = RuntimeSettings.model_validate(
        {
            "agents": {
                "delivery_planner": {
                    "name": "Delivery Planner",
                    "description": "Produces and refines delivery plans.",
                    "profile_instructions": "Write only in your Agent workspace.",
                    "contract": "planner",
                },
                "quality_reviewer": {
                    "name": "Quality Reviewer",
                    "description": "Reviews plans and delivery candidates.",
                    "profile_instructions": "Write only in your Agent workspace.",
                    "contract": "reviewer",
                },
            },
            "prompts": load_settings(DEFAULT_SETTINGS_PATH).runtime.prompts.model_dump(
                mode="json"
            ),
            "hard_gates": {"plan_approval": {"agent_id": "quality_reviewer"}},
        }
    )

    executions = compose_test_executions(project_path, settings).executions
    talk_tool = next(
        tool for tool in executions.tools.tools if tool.name == "talk_to_agent"
    )
    talk_schema = talk_tool.provider_schema()
    description = talk_schema["description"]
    assert isinstance(description, str)
    assert "delivery_planner (planner): Delivery Planner" in description
    assert "quality_reviewer (reviewer): Quality Reviewer" in description
    agent_id_schema = talk_schema["parameters"]["properties"]["agent_id"]
    assert isinstance(agent_id_schema, dict)
    assert "enum" not in agent_id_schema
    for tool in executions.tools.tools:
        parameters = tool.provider_schema()["parameters"]
        assert set(parameters["required"]) == set(parameters["properties"])
    conversation_schema = talk_schema["parameters"]["properties"][
        "conversation_id"
    ]
    assert {option.get("type") for option in conversation_schema["anyOf"]} == {
        None,
        "null",
    }


def test_cli_only_passes_explicit_runtime_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class _Runtime:
        def initialize(self) -> ProjectRuntimeState:
            return ProjectRuntimeState(triage_id="test-runtime")

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
    api_key_env = (
        load_settings(DEFAULT_SETTINGS_PATH)
        .project_owner_agent.selected_model.api_key_env
    )
    monkeypatch.delenv(api_key_env, raising=False)
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
    monkeypatch.setattr(project_owner_service, "ProjectOwnerModel", _ReplyingModel)
    project_path = initialize_git_project()
    runtime = ProjectRuntime(
        project_path=project_path,
        settings=_settings(),
        approval_mode="yolo",
        responses_transport=_UNUSED_RESPONSES_TRANSPORT,
    )
    runtime.initialize()

    runtime.submit_message("first")
    first_result = runtime.drive_next_activation()
    runtime.submit_message("second")
    second_result = runtime.drive_next_activation()
    restarted_runtime = ProjectRuntime(
        project_path=project_path,
        settings=_settings(),
        approval_mode="yolo",
        responses_transport=_UNUSED_RESPONSES_TRANSPORT,
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


def test_activation_restores_its_frozen_summary_checkpoint_after_restart(
    initialize_git_project: Callable[[], Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ReplyingModel.constructions = 0
    _ReplyingModel.queries = []
    monkeypatch.setattr(project_owner_service, "ProjectOwnerModel", _ReplyingModel)
    project_path = initialize_git_project()
    runtime = ProjectRuntime(
        project_path=project_path,
        settings=_settings(),
        approval_mode="yolo",
        responses_transport=_UNUSED_RESPONSES_TRANSPORT,
    )
    runtime.initialize()

    first_activation = runtime.submit_message("first")
    runtime.drive_next_activation()
    runtime.submit_message("second")
    runtime.drive_next_activation()

    database = SQLiteDatabase.for_project(project_path)
    owners = SQLiteProjectOwnerAgentRepository()
    messages = SQLiteMessageHistoryRepository()
    summaries = SQLiteSummaryHistoryRepository()
    with database.transaction() as connection:
        owner = owners.get_by_triage_id(connection, first_activation.triage_id)
        assert owner is not None
        histories = messages.list_by_session_id(
            connection,
            owner.project_owner_session_id,
        )
        covered_through_message_id = histories[-1].message_id
        frozen_summary = SummaryHistory(
            project_owner_session_id=owner.project_owner_session_id,
            summary_id="summary-frozen",
            covered_through_message_id=covered_through_message_id,
            intent_summary_content="Continue the current work.",
            trajectory_summary_content="First and second were already handled.",
        )
        summaries.insert(connection, frozen_summary)
        owners.update(connection, replace(owner, summary_id=frozen_summary.summary_id))

    activation = runtime.submit_message("third")
    assert activation.summary_id == frozen_summary.summary_id

    with database.transaction() as connection:
        owner = owners.get_by_triage_id(connection, activation.triage_id)
        assert owner is not None
        newer_summary = SummaryHistory(
            project_owner_session_id=owner.project_owner_session_id,
            summary_id="summary-newer",
            covered_through_message_id=activation.message_id,
            intent_summary_content="Continue the newer work.",
            trajectory_summary_content=(
                "This summary must not replace the frozen checkpoint."
            ),
        )
        summaries.insert(connection, newer_summary)
        owners.update(connection, replace(owner, summary_id=newer_summary.summary_id))

    restarted_runtime = ProjectRuntime(
        project_path=project_path,
        settings=_settings(),
        approval_mode="yolo",
        responses_transport=_UNUSED_RESPONSES_TRANSPORT,
    )
    result = restarted_runtime.drive_next_activation()

    assert result.activation is not None
    assert result.activation.summary_id == frozen_summary.summary_id
    assert result.exit is not None
    assert result.exit.content == "third"
    restored_contents = [
        message.get("content") for message in _ReplyingModel.queries[-1]
    ]
    assert len(restored_contents) == 4
    system = str(restored_contents[0])
    configured = _settings().runtime.prompts
    assert system.startswith(configured.project_owner.role.strip())
    assert "agentplanex-project-observe" in system
    assert f'"project_root": "{project_path.resolve()}"' in system
    assert f'"activation_id": "{activation.activation_id}"' in system
    assert restored_contents[1:] == [
        configured.summary_context_header.strip(),
        [
            {
                "type": "input_text",
                "text": "<intent-summary>\nContinue the current work.\n</intent-summary>",
            },
            {
                "type": "input_text",
                "text": (
                    "<trajectory-summary>\n"
                    "First and second were already handled.\n"
                    "</trajectory-summary>"
                ),
            },
        ],
        "third",
    ]


def test_activation_rejects_summary_from_another_owner_session(
    initialize_git_project: Callable[[], Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ReplyingModel.queries = []
    monkeypatch.setattr(project_owner_service, "ProjectOwnerModel", _ReplyingModel)
    project_path = initialize_git_project()
    runtime = ProjectRuntime(
        project_path=project_path,
        settings=_settings(),
        approval_mode="yolo",
        responses_transport=_UNUSED_RESPONSES_TRANSPORT,
    )
    runtime.initialize()
    first_activation = runtime.submit_message("first")
    runtime.drive_next_activation()
    _ReplyingModel.queries = []

    database = SQLiteDatabase.for_project(project_path)
    owners = SQLiteProjectOwnerAgentRepository()
    messages = SQLiteMessageHistoryRepository()
    summaries = SQLiteSummaryHistoryRepository()
    with database.transaction() as connection:
        owner = owners.get_by_triage_id(connection, first_activation.triage_id)
        assert owner is not None
        watermark = messages.get_latest_by_session_id(
            connection,
            owner.project_owner_session_id,
        )
        assert watermark is not None
        invalid_summary = SummaryHistory(
            project_owner_session_id="another-owner-session",
            summary_id="summary-wrong-session",
            covered_through_message_id=watermark.message_id,
            intent_summary_content="Continue another session.",
            trajectory_summary_content="This summary belongs to another Owner.",
        )
        summaries.insert(connection, invalid_summary)
        owners.update(connection, replace(owner, summary_id=invalid_summary.summary_id))

    activation = runtime.submit_message("second")
    result = runtime.drive_next_activation()

    assert activation.summary_id == invalid_summary.summary_id
    assert result.activation is not None
    assert result.activation.status.value == "FAILED"
    assert result.exit is not None
    assert result.exit.status is AgentExitStatus.UNHANDLED_EXCEPTION
    assert "Summary does not belong to Owner session" in result.exit.content
    assert _ReplyingModel.queries == []


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
    monkeypatch.setattr(project_owner_service, "ProjectOwnerModel", _BashCallingModel)
    project_path = initialize_git_project()
    runtime = ProjectRuntime(
        project_path=project_path,
        settings=_settings(
            bash_timeout_seconds=timeout_seconds,
            bash_output_limit=output_limit,
        ),
        approval_mode="yolo",
        responses_transport=_UNUSED_RESPONSES_TRANSPORT,
    )
    runtime.initialize()

    runtime.submit_message(command)
    result = runtime.drive_next_activation().exit
    assert result is not None

    assert expected in result.content


def test_project_owner_bash_fails_closed_without_bubblewrap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(local_shell_module.shutil, "which", lambda *_args, **_kwargs: None)

    result = local_shell_module.run_local_shell(
        "printf unsafe > escaped",
        cwd=tmp_path,
    )

    assert result == {
        "output": "",
        "returncode": -1,
        "exception_info": "Bubblewrap is required for Project Owner Bash",
    }
    assert not (tmp_path / "escaped").exists()


def test_project_owner_bash_runs_below_private_tmp() -> None:
    with TemporaryDirectory(prefix="agentplanex-bwrap-test-", dir="/tmp") as directory:
        project_path = Path(directory)
        result = local_shell_module.run_local_shell(
            "printf 'inside-tmp\\n' > probe && test ! -e /run/docker.sock",
            cwd=project_path,
        )

        assert result == {
            "output": "",
            "returncode": 0,
            "exception_info": "",
        }
        assert (project_path / "probe").read_text(encoding="utf-8") == "inside-tmp\n"


def test_project_owner_bash_cannot_reach_a_host_tcp_listener(tmp_path: Path) -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        command = (
            "python -c \"import socket; "
            f"socket.create_connection(('127.0.0.1', {port}), 0.2)\""
        )

        result = local_shell_module.run_local_shell(command, cwd=tmp_path)

    assert result["returncode"] != 0
    assert result["exception_info"] == ""


def test_sandbox_denial_blocks_until_the_user_sends_another_message(
    initialize_git_project: Callable[[], Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _PolicyAwareBashModel.calls = 0
    _PolicyAwareBashModel.observation = None
    monkeypatch.setattr(project_owner_service, "ProjectOwnerModel", _PolicyAwareBashModel)
    project_path = initialize_git_project()
    runtime = ProjectRuntime(
        project_path=project_path,
        settings=_settings(),
        approval_mode="yolo",
        responses_transport=_UNUSED_RESPONSES_TRANSPORT,
    )
    runtime.initialize()

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        command = (
            "python -c \"import socket; "
            f"socket.create_connection(('127.0.0.1', {port}), 0.2)\""
        )
        runtime.submit_message(command)
        result = runtime.drive_next_activation()

    assert result.exit is not None
    assert result.exit.status is AgentExitStatus.REPLY_TO_HUMAN
    assert _PolicyAwareBashModel.calls == 2
    assert _PolicyAwareBashModel.observation is not None
    assert _PolicyAwareBashModel.observation["user_action_required"] is True

    blocked = runtime.project_control_view().context
    assert blocked.status == "BLOCKED"
    assert blocked.blocked_capability == "network"
    assert blocked.blocked_previous_status == "TODO"
    assert blocked.blocked_reason is not None

    denied_retry = runtime.execute_action(
        {"tool": "bash", "arguments": {"command": "touch should-not-exist"}}
    )
    assert denied_retry.output["error_type"] == "USER_INTERVENTION_REQUIRED"
    assert not (project_path / "should-not-exist").exists()

    runtime.submit_message("Continue without network access.")
    resumed = runtime.project_control_view().context
    assert resumed.status == "TODO"
    assert resumed.blocked_reason is None
    assert resumed.blocked_capability is None
    assert resumed.blocked_previous_status is None
