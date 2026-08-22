"""Behavioral tests for the shared model-visible Tool Call contract."""

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from agentplanex.infrastructure.sqlite import SQLiteDatabase
from agentplanex.infrastructure.sqlite.repositories import (
    SQLiteExecutionEventRepository,
)
from agentplanex.project_owner_agent.models.responses import (
    ProjectOwnerModel,
    ResponsesClient,
    ResponsesRequest,
)
from agentplanex.project_owner_agent.tools import ToolArgumentError
from agentplanex.settings import DEFAULT_SETTINGS_PATH, load_settings
from tests.runtime_support import compose_test_executions


def test_tool_catalog_treats_empty_conversation_id_as_a_new_conversation(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    executions = compose_test_executions(
        project_path,
        load_settings(DEFAULT_SETTINGS_PATH).runtime,
    ).executions
    arguments = {
        "agent_id": "planner",
        "kind": "task",
        "message": "Create plan.md.",
        "conversation_id": "",
        "artifacts": [],
    }

    action = executions.tools.create_action(
        name="talk_to_agent",
        call_id="call-1",
        arguments=arguments,
    )

    assert action == {
        "tool": "talk_to_agent",
        "call_id": "call-1",
        "arguments": {**arguments, "conversation_id": None},
    }


def test_provider_schema_and_runtime_share_the_conversation_id_contract(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    tools = compose_test_executions(
        project_path,
        load_settings(DEFAULT_SETTINGS_PATH).runtime,
    ).executions.tools
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
    tools = compose_test_executions(
        project_path,
        load_settings(DEFAULT_SETTINGS_PATH).runtime,
    ).executions.tools
    provider_schema = next(
        item for item in tools.provider_schemas() if item["name"] == "update_milestones"
    )["parameters"]
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
        provider_errors = list(
            Draft202012Validator(provider_schema).iter_errors(arguments)
        )
        if index == 2:
            # OpenAI's strict JSON Schema subset cannot express array `contains`;
            # the authoritative Runtime validator still enforces this invariant.
            assert not provider_errors
        else:
            assert provider_errors
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
    tools = compose_test_executions(
        project_path,
        load_settings(DEFAULT_SETTINGS_PATH).runtime,
    ).executions.tools
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

    provider_schemas = tools.provider_schemas()
    assert {schema["name"] for schema in provider_schemas} == {
        "bash",
        "request_plan_approval",
        "talk_to_agent",
        "update_milestones",
        "run_next_milestone",
        "decide_milestone_candidate",
    }
    assert "$ref" not in json.dumps(provider_schemas)
    assert "$defs" not in json.dumps(provider_schemas)


def test_direct_execution_uses_the_same_argument_contract(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    composed = compose_test_executions(
        project_path,
        load_settings(DEFAULT_SETTINGS_PATH).runtime,
    )
    executions = composed.executions

    result = executions.execute(
        composed.state,
        {
            "tool": "talk_to_agent",
            "call_id": "call-1",
            "arguments": {
                "agent_id": "planner",
                "kind": "task",
                "message": "Create plan.md.",
                "conversation_id": "not-an-apx-id",
                "artifacts": [],
            },
        },
    )

    assert result.output["ok"] is False
    assert result.output["error"]["code"] == "INVALID_TOOL_ARGUMENTS"
    assert "conversation_id" in result.output["error"]["message"]
    assert result.output["error"]["retryable"] is True


def test_candidate_identity_is_rejected_by_tool_contract_before_delivery(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    composed = compose_test_executions(
        project_path,
        load_settings(DEFAULT_SETTINGS_PATH).runtime,
    )

    result = composed.executions.execute(
        composed.state,
        {
            "tool": "decide_milestone_candidate",
            "call_id": "invalid-candidate-identity",
            "arguments": {
                "snapshot_id": "snapshot-1",
                "run_id": "bad/run",
                "milestone_key": "milestone-1",
                "candidate_commit_sha": "abc123",
                "decision": "accept",
                "reason": "This must not reach Delivery.",
            },
        },
    )

    assert result.output["ok"] is False
    assert result.output["error"]["code"] == "INVALID_TOOL_ARGUMENTS"
    assert result.output["error"]["retryable"] is True
    assert composed.context.state() == composed.state


def test_unknown_agent_is_rejected_before_an_invocation_event(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    composed = compose_test_executions(
        project_path,
        load_settings(DEFAULT_SETTINGS_PATH).runtime,
    )

    result = composed.executions.execute(
        composed.state,
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
    database = SQLiteDatabase.for_project(project_path)
    with database.read_only_connection() as connection:
        events = SQLiteExecutionEventRepository().list_by_triage_id(
            connection,
            composed.state.triage_id,
        )
    assert events == ()


def test_model_tool_call_normalizes_empty_conversation_id_before_action(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    tools = compose_test_executions(
        project_path,
        load_settings(DEFAULT_SETTINGS_PATH).runtime,
    ).executions.tools

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

    message = model.query([{"role": "system", "content": "Test Owner."}])

    assert message["extra"]["actions"] == [
        {
            "tool": "talk_to_agent",
            "call_id": "call-invalid",
            "arguments": {
                "agent_id": "planner",
                "kind": "task",
                "message": "Create plan.md.",
                "conversation_id": None,
                "artifacts": [],
            },
        }
    ]


def test_model_preserves_call_ids_for_multiple_tool_calls(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    tools = compose_test_executions(
        project_path,
        load_settings(DEFAULT_SETTINGS_PATH).runtime,
    ).executions.tools

    class MultipleToolCallTransport:
        def create(self, _request: ResponsesRequest) -> object:
            return {
                "object": "response",
                "output": [
                    {
                        "type": "function_call",
                        "name": "bash",
                        "call_id": "call-read-requirements",
                        "arguments": json.dumps(
                            {"command": "sed -n '1,80p' requirements.md"}
                        ),
                    },
                    {
                        "type": "function_call",
                        "name": "bash",
                        "call_id": "call-read-architecture",
                        "arguments": json.dumps(
                            {"command": "sed -n '1,80p' architecture.md"}
                        ),
                    },
                ],
            }

    model = ProjectOwnerModel(
        tools=tools,
        responses=ResponsesClient(
            model="test-model",
            transport=MultipleToolCallTransport(),
        ),
    )

    message = model.query([{"role": "system", "content": "Test Owner."}])

    assert message["extra"]["actions"] == [
        {
            "tool": "bash",
            "call_id": "call-read-requirements",
            "arguments": {"command": "sed -n '1,80p' requirements.md"},
        },
        {
            "tool": "bash",
            "call_id": "call-read-architecture",
            "arguments": {"command": "sed -n '1,80p' architecture.md"},
        },
    ]


def test_response_history_preserves_call_id_without_output_only_status() -> None:
    requests: list[ResponsesRequest] = []

    class RecordingTransport:
        def create(self, request: ResponsesRequest) -> object:
            requests.append(request)
            return {
                "object": "response",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "done"}],
                    }
                ],
            }

    responses = ResponsesClient(model="test-model", transport=RecordingTransport())
    responses.request(
        [
            {"role": "system", "content": "Test Owner."},
            {
                "object": "response",
                "output": [
                    {
                        "type": "function_call",
                        "id": "provider-item-id",
                        "call_id": "call-preserved",
                        "name": "bash",
                        "arguments": '{"command":"pwd"}',
                        "status": "completed",
                    }
                ],
            },
            {
                "type": "function_call_output",
                "call_id": "call-preserved",
                "output": '{"ok":true}',
            },
        ],
        tools=None,
        tool_choice="none",
    )

    function_call = requests[0].input[0]
    assert function_call["call_id"] == "call-preserved"
    assert "status" not in function_call
