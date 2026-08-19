"""Behavioral tests for the shared model-visible Tool Call contract."""

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from agentplanex.domains import ProjectRuntimeContext
from agentplanex.project_owner_agent.exception import FormatError
from agentplanex.project_owner_agent.models.responses import (
    ProjectOwnerModel,
    ResponsesClient,
    ResponsesRequest,
)
from agentplanex.project_owner_agent.tools import ToolArgumentError
from agentplanex.project_runtime.executions import create_project_executions
from agentplanex.services.event_bus import EventBus
from agentplanex.settings import DEFAULT_SETTINGS_PATH, load_settings


def test_tool_catalog_rejects_empty_conversation_id_before_execution(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    executions = create_project_executions(
        project_path,
        load_settings(DEFAULT_SETTINGS_PATH).runtime,
    )
    arguments = {
        "agent_id": "planner",
        "kind": "task",
        "message": "Create plan.md.",
        "conversation_id": "",
        "artifacts": [],
    }

    with pytest.raises(ToolArgumentError, match="conversation_id"):
        executions.tools.create_action(
            name="talk_to_agent",
            call_id="call-1",
            arguments=arguments,
        )

    arguments["conversation_id"] = None
    action = executions.tools.create_action(
        name="talk_to_agent",
        call_id="call-2",
        arguments=arguments,
    )

    assert action == {
        "tool": "talk_to_agent",
        "call_id": "call-2",
        "arguments": arguments,
    }


def test_provider_schema_and_runtime_share_the_conversation_id_contract(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    tools = create_project_executions(
        project_path,
        load_settings(DEFAULT_SETTINGS_PATH).runtime,
    ).tools
    schema = next(
        item for item in tools.provider_schemas() if item["name"] == "talk_to_agent"
    )["parameters"]
    arguments = {
        "agent_id": "planner",
        "kind": "task",
        "message": "Create plan.md.",
        "conversation_id": "",
        "artifacts": [],
    }

    assert list(Draft202012Validator(schema).iter_errors(arguments))

    arguments["conversation_id"] = None
    assert not list(Draft202012Validator(schema).iter_errors(arguments))


def test_tool_catalog_rejects_invalid_complete_milestone_views(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    tools = create_project_executions(
        project_path,
        load_settings(DEFAULT_SETTINGS_PATH).runtime,
    ).tools
    milestone = {
        "key": "m1",
        "objective": "Ship the first playable slice.",
        "state": "pending",
        "stages": [{"key": "s1", "objective": "Build the slice."}],
    }
    invalid_arguments = (
        {"reason": "", "milestones": [milestone]},
        {"reason": "Initial delivery plan.", "milestones": []},
        {
            "reason": "Initial delivery plan.",
            "milestones": [{**milestone, "state": "completed"}],
        },
        {
            "reason": "Initial delivery plan.",
            "milestones": [{**milestone, "key": "invalid key"}],
        },
        {
            "reason": "Initial delivery plan.",
            "milestones": [{**milestone, "objective": " "}],
        },
        {
            "reason": "Initial delivery plan.",
            "milestones": [{**milestone, "stages": []}],
        },
        {
            "reason": "Initial delivery plan.",
            "milestones": [
                {
                    **milestone,
                    "stages": [{"key": "invalid key", "objective": "Build."}],
                }
            ],
        },
        {
            "reason": "Initial delivery plan.",
            "milestones": [
                {**milestone, "stages": [{"key": "s1", "objective": " "}]}
            ],
        },
    )

    for index, arguments in enumerate(invalid_arguments):
        with pytest.raises(ToolArgumentError):
            tools.create_action(
                name="update_milestones",
                call_id=f"invalid-{index}",
                arguments=arguments,
            )

    action = tools.create_action(
        name="update_milestones",
        call_id="valid",
        arguments={"reason": "Initial delivery plan.", "milestones": [milestone]},
    )
    assert action["arguments"] == {
        "reason": "Initial delivery plan.",
        "milestones": [milestone],
    }


def test_every_tool_uses_the_shared_argument_contract(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    tools = create_project_executions(
        project_path,
        load_settings(DEFAULT_SETTINGS_PATH).runtime,
    ).tools
    invalid_calls = (
        ("bash", {"command": " "}),
        (
            "talk_to_agent",
            {
                "agent_id": "",
                "kind": "message",
                "message": "Inspect this.",
                "conversation_id": None,
                "artifacts": [],
            },
        ),
        (
            "talk_to_agent",
            {
                "agent_id": "planner",
                "kind": "message",
                "message": " ",
                "conversation_id": None,
                "artifacts": [],
            },
        ),
        (
            "talk_to_agent",
            {
                "agent_id": "planner",
                "kind": "message",
                "message": "Inspect this.",
                "conversation_id": None,
                "artifacts": [{"uri": "https://example.com/input.md"}],
            },
        ),
        (
            "decide_milestone_candidate",
            {"decision": "accept", "reason": " "},
        ),
        ("request_plan_approval", {"unexpected": True}),
        ("run_next_milestone", {"unexpected": True}),
    )

    for index, (name, arguments) in enumerate(invalid_calls):
        with pytest.raises(ToolArgumentError):
            tools.create_action(
                name=name,
                call_id=f"invalid-{index}",
                arguments=arguments,
            )

    assert {schema["name"] for schema in tools.provider_schemas()} == {
        "bash",
        "request_plan_approval",
        "talk_to_agent",
        "update_milestones",
        "run_next_milestone",
        "decide_milestone_candidate",
    }


def test_direct_execution_uses_the_same_argument_contract(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    executions = create_project_executions(
        project_path,
        load_settings(DEFAULT_SETTINGS_PATH).runtime,
    )

    result = executions.execute(
        ProjectRuntimeContext(triage_id="triage-tool-contract"),
        {
            "tool": "talk_to_agent",
            "call_id": "call-1",
            "arguments": {
                "agent_id": "planner",
                "kind": "task",
                "message": "Create plan.md.",
                "conversation_id": "",
                "artifacts": [],
            },
        },
    )

    assert result.output["ok"] is False
    assert result.output["error"]["code"] == "INVALID_TOOL_ARGUMENTS"
    assert "conversation_id" in result.output["error"]["message"]
    assert result.output["error"]["retryable"] is True


def test_unknown_agent_is_rejected_before_an_invocation_event(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    events: list[object] = []
    executions = create_project_executions(
        project_path,
        load_settings(DEFAULT_SETTINGS_PATH).runtime,
        event_bus=EventBus(handlers=(events.append,)),
    )

    result = executions.execute(
        ProjectRuntimeContext(triage_id="triage-tool-contract"),
        {
            "tool": "talk_to_agent",
            "call_id": "call-unknown-agent",
            "arguments": {
                "agent_id": "not_configured",
                "kind": "message",
                "message": "Inspect this.",
                "conversation_id": None,
                "artifacts": [],
            },
        },
    )

    assert result.output["ok"] is False
    assert "Unknown Agent" in result.output["error"]
    assert events == []


def test_model_tool_call_is_validated_before_becoming_an_action(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    tools = create_project_executions(
        project_path,
        load_settings(DEFAULT_SETTINGS_PATH).runtime,
    ).tools

    class InvalidToolCallTransport:
        def create(self, _request: ResponsesRequest) -> object:
            return {
                "object": "response",
                "output": [
                    {
                        "type": "function_call",
                        "name": "talk_to_agent",
                        "call_id": "call-invalid",
                        "arguments": json.dumps(
                            {
                                "agent_id": "planner",
                                "kind": "task",
                                "message": "Create plan.md.",
                                "conversation_id": "",
                                "artifacts": [],
                            }
                        ),
                    }
                ],
            }

    model = ProjectOwnerModel(
        tools=tools,
        responses=ResponsesClient(
            model="test-model",
            transport=InvalidToolCallTransport(),
        ),
    )

    with pytest.raises(FormatError, match="conversation_id"):
        model.query([{"role": "system", "content": "Test Owner."}])
