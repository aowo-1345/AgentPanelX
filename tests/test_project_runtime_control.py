"""Behavior and boundary tests for privileged Project Runtime control."""

from collections.abc import Callable
from pathlib import Path

import pytest

from agentplanex.bootstrap import (
    create_project_control_query,
    create_project_runtime,
    create_project_runtime_control,
)
from agentplanex.infrastructure.sqlite import SQLiteDatabase
from agentplanex.infrastructure.sqlite.repositories import (
    SQLiteExecutionEventRepository,
    SQLiteMessageHistoryRepository,
    SQLiteOwnerActivationRepository,
    SQLiteProjectOwnerAgentRepository,
)
from agentplanex.project_owner_agent.models.responses import (
    ResponsesRequest,
    ResponsesTransport,
)
from agentplanex.project_runtime.control import ProjectRuntimeControl
from agentplanex.project_runtime.runtime import ProjectRuntime
from agentplanex.settings import DEFAULT_SETTINGS_PATH, load_settings


class _UnusedResponses(ResponsesTransport):
    def create(self, _request: ResponsesRequest) -> object:
        raise AssertionError("Manual Control must not call the Owner model")


def _runtime(project_path: Path) -> ProjectRuntime:
    return create_project_runtime(
        project_path=project_path,
        settings=load_settings(DEFAULT_SETTINGS_PATH),
        approval_mode="yolo",
        responses_transport=_UnusedResponses(),
    )


def _control(project_path: Path) -> ProjectRuntimeControl:
    return create_project_runtime_control(
        project_path=project_path,
        settings=load_settings(DEFAULT_SETTINGS_PATH),
        approval_mode="yolo",
        responses_transport=_UnusedResponses(),
    )


def test_control_drives_the_same_durable_owner_activation(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    runtime = _runtime(project_path)
    runtime.initialize()
    runtime.begin_feature()
    submitted = runtime.submit_message("Keep this activation identity.")
    control = _control(project_path)

    stepped = control.drive_owner_tool(
        {
            "tool": "bash",
            "call_id": "manual-step",
            "arguments": {"command": "printf controlled"},
        }
    )
    finished = control.reply_owner("The controlled step is complete.")

    assert stepped.activation.activation_id == submitted.activation_id
    assert finished.activation.activation_id == submitted.activation_id
    assert finished.activation.status.value == "COMPLETED"
    database = SQLiteDatabase.for_project(project_path)
    with database.read_only_connection() as connection:
        activations = SQLiteOwnerActivationRepository().list_by_triage_id(
            connection,
            submitted.triage_id,
        )
        owner = SQLiteProjectOwnerAgentRepository().get_by_triage_id(
            connection,
            submitted.triage_id,
        )
        assert owner is not None
        histories = SQLiteMessageHistoryRepository().list_by_session_id(
            connection,
            owner.project_owner_session_id,
        )
        events = SQLiteExecutionEventRepository().list_by_triage_id(
            connection,
            submitted.triage_id,
        )
    assert [item.activation_id for item in activations] == [submitted.activation_id]
    assert any(
        message.get("content") == "The controlled step is complete."
        for history in histories
        for message in history.message
    )
    assert submitted.activation_id in {
        event.react_loop_id for event in events if event.react_loop_id is not None
    }


def test_bare_tool_cannot_bypass_owner_activation_or_fabricate_one_when_idle(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    runtime = _runtime(project_path)
    state = runtime.initialize()
    runtime.begin_feature()
    runtime.submit_message("Do not bypass this activation.")
    control = _control(project_path)
    action = {
        "tool": "bash",
        "call_id": "bare-tool",
        "arguments": {"command": "printf controlled"},
    }

    with pytest.raises(ValueError, match="unfinished activation"):
        control.execute_tool(action)
    control.drive_owner_tool(action)
    control.reply_owner("Release the activation.")
    before = create_project_control_query(project_path=project_path).get_current()
    result = control.execute_tool({**action, "call_id": "idle-bare-tool"})
    after = create_project_control_query(project_path=project_path).get_current()

    assert "error" not in result.output
    assert before.owner_activation is None
    assert after.owner_activation is None
    database = SQLiteDatabase.for_project(project_path)
    with database.read_only_connection() as connection:
        assert (
            len(
                SQLiteOwnerActivationRepository().list_by_triage_id(
                    connection,
                    state.triage_id,
                )
            )
            == 1
        )


def test_control_rejects_missing_project_before_composition(tmp_path: Path) -> None:
    missing = tmp_path / "missing-project"

    with pytest.raises(ValueError, match="Project path is not a directory"):
        _control(missing)

    assert not missing.exists()
