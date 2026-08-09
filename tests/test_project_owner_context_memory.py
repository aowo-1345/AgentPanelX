"""End-to-end Project Owner context-memory behavior."""

import json
from collections.abc import Callable
from pathlib import Path
from threading import Lock

import pytest

from agentplanex.infrastructure.sqlite import SQLiteDatabase
from agentplanex.infrastructure.sqlite.repositories import (
    SQLiteProjectOwnerAgentRepository,
    SQLiteSummaryHistoryRepository,
)
from agentplanex.project_owner_agent.models.jbb import (
    ResponsesRequest,
    ResponsesTransport,
)
from agentplanex.project_runtime import ProjectRuntime
from agentplanex.settings import (
    DEFAULT_SETTINGS_PATH,
    ContextMemorySettings,
    Settings,
    load_settings,
)


class _ContextMemoryTransport(ResponsesTransport):
    def __init__(
        self,
        prompts: tuple[str, str, str],
        *,
        summary_failure: str | None = None,
        first_owner_tool: bool = False,
    ) -> None:
        self.prompts = prompts
        self.summary_failure = summary_failure
        self.first_owner_tool = first_owner_tool
        self.requests: list[ResponsesRequest] = []
        self.owner_request_count = 0
        self._lock = Lock()

    def create(self, request: ResponsesRequest) -> object:
        with self._lock:
            self.requests.append(request)
        task = str(request.input[-1].get("content", ""))
        trajectory, initial_intent, update_intent = self.prompts
        if task == trajectory:
            if self.summary_failure == "trajectory-error":
                raise RuntimeError("trajectory unavailable")
            if self.summary_failure == "trajectory-tool":
                return _tool_response("bash", "summary-tool", {"command": "pwd"})
            return _text_response(
                "<trajectory-summary>Inspected the long initial request.</trajectory-summary>"
            )
        if task == initial_intent:
            if self.summary_failure == "intent-xml":
                return _text_response("not valid summary xml")
            if self.summary_failure == "intent-empty":
                return _text_response("<intent-summary> </intent-summary>")
            if self.summary_failure == "intent-outside":
                return _text_response(
                    "outside<intent-summary>invalid</intent-summary>"
                )
            if self.summary_failure == "intent-duplicate":
                return _text_response(
                    "<intent-summary>first</intent-summary>"
                    "<intent-summary>second</intent-summary>"
                )
            return _text_response(
                "<intent-summary>Deliver the requested context-memory behavior.</intent-summary>"
            )
        if task == update_intent:
            return _text_response(
                "<intent-summary>"
                "Deliver the context-memory behavior and its update."
                "</intent-summary>"
            )
        with self._lock:
            owner_index = self.owner_request_count
            self.owner_request_count += 1
        if self.first_owner_tool and owner_index == 0:
            return _tool_response(
                "bash",
                "large-observation",
                {
                    "command": (
                        "for i in $(seq 1 500); do printf 'observation '; done"
                    )
                },
            )
        return _text_response("owner-finished")


def _text_response(text: str) -> object:
    return {
        "object": "response",
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text}],
            }
        ]
    }


def _tool_response(
    name: str,
    call_id: str,
    arguments: dict[str, object],
) -> object:
    return {
        "object": "response",
        "output": [
            {
                "type": "function_call",
                "name": name,
                "call_id": call_id,
                "arguments": json.dumps(arguments),
            }
        ]
    }


def _settings_and_transport(
    *,
    capacity_tokens: int,
    summary_failure: str | None = None,
    first_owner_tool: bool = False,
) -> tuple[Settings, _ContextMemoryTransport]:
    configured = load_settings(DEFAULT_SETTINGS_PATH)
    prompts = configured.runtime.prompts
    settings = configured.model_copy(
        update={
            "project_owner_agent": configured.project_owner_agent.model_copy(
                update={
                    "context_memory": ContextMemorySettings(
                        capacity_tokens=capacity_tokens,
                        compaction_threshold=0.8,
                    )
                }
            )
        }
    )
    return settings, _ContextMemoryTransport(
        (
            prompts.trajectory_summary.strip(),
            prompts.initial_intent_summary.strip(),
            prompts.update_intent_summary.strip(),
        ),
        summary_failure=summary_failure,
        first_owner_tool=first_owner_tool,
    )


def test_owner_naturally_compacts_a_long_query_before_replying(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    settings, transport = _settings_and_transport(capacity_tokens=1_500)
    prompts = settings.runtime.prompts
    runtime = ProjectRuntime(
        project_path=project_path,
        settings=settings,
        approval_mode="yolo",
        responses_transport=transport,
    )

    activation = runtime.submit_message("context " * 600)
    driven = runtime.drive_next_activation()

    assert driven.exit is not None
    assert driven.exit.content == "owner-finished"
    assert driven.activation is not None
    assert driven.activation.summary_id is not None

    summary_requests = [
        request for request in transport.requests if request.tool_choice == "none"
    ]
    owner_requests = [
        request for request in transport.requests if request.tool_choice == "auto"
    ]
    assert len(summary_requests) == 2
    assert len(owner_requests) == 1
    assert all(request.tools == owner_requests[0].tools for request in summary_requests)
    assert all(
        request.input[:-1] == summary_requests[0].input[:-1]
        for request in summary_requests
    )
    assert owner_requests[0].input == (
        {
            "role": "developer",
            "content": prompts.summary_context_header.strip(),
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": (
                        "<intent-summary>\n"
                        "Deliver the requested context-memory behavior.\n"
                        "</intent-summary>"
                    ),
                },
                {
                    "type": "input_text",
                    "text": (
                        "<trajectory-summary>\n"
                        "Inspected the long initial request.\n"
                        "</trajectory-summary>"
                    ),
                },
            ],
        },
    )

    database = SQLiteDatabase.for_project(project_path)
    owners = SQLiteProjectOwnerAgentRepository()
    summaries = SQLiteSummaryHistoryRepository()
    with database.read_only_connection() as connection:
        owner = owners.get_by_triage_id(connection, activation.triage_id)
        assert owner is not None
        assert owner.summary_id == driven.activation.summary_id
        summary = summaries.get(connection, owner.summary_id)
    assert summary is not None
    assert summary.covered_through_message_id == activation.message_id
    assert summary.intent_summary_content == (
        "Deliver the requested context-memory behavior."
    )
    assert summary.trajectory_summary_content == (
        "Inspected the long initial request."
    )

    event_types = [
        event.event_type.value for event in runtime.project_control_view().timeline
    ]
    assert event_types[-4:] == [
        "REACT_LOOP_ENTERED",
        "CONTEXT_COMPACTION_STARTED",
        "CONTEXT_COMPACTION_COMPLETED",
        "REACT_LOOP_EXITED",
    ]


def test_owner_below_threshold_queries_without_compaction(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    settings, transport = _settings_and_transport(capacity_tokens=128_000)
    runtime = ProjectRuntime(
        project_path=project_path,
        settings=settings,
        approval_mode="yolo",
        responses_transport=transport,
    )

    runtime.submit_message("short request")
    driven = runtime.drive_next_activation()

    assert driven.exit is not None
    assert driven.exit.content == "owner-finished"
    assert [request.tool_choice for request in transport.requests] == ["auto"]
    assert not any(
        event.event_type.value.startswith("CONTEXT_COMPACTION")
        for event in runtime.project_control_view().timeline
    )


def test_second_compaction_rolls_intent_and_replaces_trajectory(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    settings, transport = _settings_and_transport(capacity_tokens=1_500)
    prompts = settings.runtime.prompts
    runtime = ProjectRuntime(
        project_path=project_path,
        settings=settings,
        approval_mode="yolo",
        responses_transport=transport,
    )

    runtime.submit_message("first-context " * 600)
    first = runtime.drive_next_activation()
    runtime.submit_message("updated-context " * 600)
    second = runtime.drive_next_activation()

    assert first.activation is not None
    assert second.activation is not None
    assert first.activation.summary_id is not None
    assert second.activation.summary_id is not None
    assert first.activation.summary_id != second.activation.summary_id
    tasks = [
        str(request.input[-1].get("content", ""))
        for request in transport.requests
        if request.tool_choice == "none"
    ]
    assert tasks.count(prompts.initial_intent_summary.strip()) == 1
    assert tasks.count(prompts.update_intent_summary.strip()) == 1
    assert tasks.count(prompts.trajectory_summary.strip()) == 2

    database = SQLiteDatabase.for_project(project_path)
    summaries = SQLiteSummaryHistoryRepository()
    with database.read_only_connection() as connection:
        first_summary = summaries.get(connection, first.activation.summary_id)
        second_summary = summaries.get(connection, second.activation.summary_id)
    assert first_summary is not None
    assert second_summary is not None
    assert first_summary.intent_summary_content == (
        "Deliver the requested context-memory behavior."
    )
    assert second_summary.intent_summary_content == (
        "Deliver the context-memory behavior and its update."
    )


def test_large_tool_observation_compacts_before_the_next_owner_query(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    settings, transport = _settings_and_transport(
        capacity_tokens=1_500,
        first_owner_tool=True,
    )
    runtime = ProjectRuntime(
        project_path=project_path,
        settings=settings,
        approval_mode="yolo",
        responses_transport=transport,
    )

    runtime.submit_message("Inspect the project before replying")
    driven = runtime.drive_next_activation()

    assert driven.exit is not None
    assert driven.exit.content == "owner-finished"
    assert [request.tool_choice for request in transport.requests] == [
        "auto",
        "none",
        "none",
        "auto",
    ]
    assert driven.activation is not None
    assert driven.activation.summary_id is None
    events = runtime.project_control_view().timeline
    completed = [
        event
        for event in events
        if event.event_type.value == "CONTEXT_COMPACTION_COMPLETED"
    ]
    assert len(completed) == 1
    assert completed[0].payload["query_index"] == 1


@pytest.mark.parametrize(
    "summary_failure",
    [
        "trajectory-error",
        "trajectory-tool",
        "intent-xml",
        "intent-empty",
        "intent-outside",
        "intent-duplicate",
    ],
)
def test_summary_failure_keeps_original_context_and_does_not_fail_activation(
    initialize_git_project: Callable[[], Path],
    summary_failure: str,
) -> None:
    project_path = initialize_git_project()
    settings, transport = _settings_and_transport(
        capacity_tokens=1_500,
        summary_failure=summary_failure,
    )
    runtime = ProjectRuntime(
        project_path=project_path,
        settings=settings,
        approval_mode="yolo",
        responses_transport=transport,
    )

    activation = runtime.submit_message("original-context " * 600)
    driven = runtime.drive_next_activation()

    assert driven.exit is not None
    assert driven.exit.content == "owner-finished"
    assert driven.activation is not None
    assert driven.activation.status.value == "COMPLETED"
    assert driven.activation.summary_id is None
    owner_request = next(
        request for request in transport.requests if request.tool_choice == "auto"
    )
    assert str(owner_request.input[-1].get("content", "")).startswith(
        "original-context"
    )

    database = SQLiteDatabase.for_project(project_path)
    owners = SQLiteProjectOwnerAgentRepository()
    with database.read_only_connection() as connection:
        owner = owners.get_by_triage_id(connection, activation.triage_id)
    assert owner is not None
    assert owner.summary_id is None
    assert [
        event.event_type.value for event in runtime.project_control_view().timeline
    ].count("CONTEXT_COMPACTION_FAILED") == 1
