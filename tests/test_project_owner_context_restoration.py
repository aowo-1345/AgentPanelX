"""Restart-safe and historical Project Owner context restoration."""

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from threading import Lock

import pytest

from agentplanex.infrastructure.sqlite import SQLiteDatabase
from agentplanex.infrastructure.sqlite.repositories import (
    SQLiteProjectOwnerAgentRepository,
)
from agentplanex.project_owner_agent.models.jbb import (
    ResponsesRequest,
    ResponsesTransport,
)
from agentplanex.project_runtime import ProjectRuntime
from agentplanex.services.owner_context import ProjectOwnerContextQuery
from agentplanex.settings import (
    DEFAULT_SETTINGS_PATH,
    ContextMemorySettings,
    Settings,
    load_settings,
)


class _RestorationTransport(ResponsesTransport):
    def __init__(self, summary_prompts: tuple[str, str, str]) -> None:
        self.summary_prompts = summary_prompts
        self.requests: list[ResponsesRequest] = []
        self._lock = Lock()

    def create(self, request: ResponsesRequest) -> object:
        with self._lock:
            self.requests.append(request)
        task = str(request.input[-1].get("content", ""))
        trajectory, initial_intent, update_intent = self.summary_prompts
        if task == trajectory:
            return _text_response(
                "<trajectory-summary>Persisted trajectory checkpoint.</trajectory-summary>"
            )
        if task == initial_intent:
            return _text_response(
                "<intent-summary>Preserve the original user intent.</intent-summary>"
            )
        if task == update_intent:
            return _text_response(
                "<intent-summary>Preserve the updated user intent.</intent-summary>"
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


def _settings(capacity_tokens: int = 128_000) -> Settings:
    configured = load_settings(DEFAULT_SETTINGS_PATH)
    return configured.model_copy(
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


def _transport(settings: Settings) -> _RestorationTransport:
    prompts = settings.runtime.prompts
    return _RestorationTransport(
        (
            prompts.trajectory_summary.strip(),
            prompts.initial_intent_summary.strip(),
            prompts.update_intent_summary.strip(),
        )
    )


def test_restart_preserves_the_persisted_owner_prompt_and_tool_contract(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    settings = _settings()
    first_transport = _transport(settings)
    runtime = ProjectRuntime(
        project_path=project_path,
        settings=settings,
        approval_mode="yolo",
        responses_transport=first_transport,
    )
    activation = runtime.submit_message("create the persistent owner")
    runtime.drive_next_activation()

    database = SQLiteDatabase.for_project(project_path)
    owners = SQLiteProjectOwnerAgentRepository()
    with database.transaction() as connection:
        owner = owners.get_by_triage_id(connection, activation.triage_id)
        assert owner is not None
        owners.update(
            connection,
            replace(
                owner,
                system_prompt="Persisted Owner identity.",
                tools=("bash", "talk_to_agent"),
            ),
        )

    prompts = settings.runtime.prompts
    changed_prompts = prompts.model_copy(
        update={
            "project_owner": prompts.project_owner.model_copy(
                update={"role": "A different configured Owner."}
            )
        }
    )
    changed_settings = settings.model_copy(
        update={
            "runtime": settings.runtime.model_copy(
                update={"prompts": changed_prompts}
            )
        }
    )
    restarted_transport = _transport(changed_settings)
    restarted = ProjectRuntime(
        project_path=project_path,
        settings=changed_settings,
        approval_mode="yolo",
        responses_transport=restarted_transport,
    )

    restarted.submit_message("continue after restart")
    driven = restarted.drive_next_activation()

    assert driven.exit is not None
    assert driven.exit.content == "owner-finished"
    owner_request = next(
        request
        for request in restarted_transport.requests
        if request.tool_choice == "auto"
    )
    assert owner_request.instructions.startswith("Persisted Owner identity.")
    assert [schema["name"] for schema in owner_request.tools] == [
        "bash",
        "talk_to_agent",
    ]


def test_restart_fails_when_a_persisted_owner_tool_no_longer_exists(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    settings = _settings()
    runtime = ProjectRuntime(
        project_path=project_path,
        settings=settings,
        approval_mode="yolo",
        responses_transport=_transport(settings),
    )
    activation = runtime.submit_message("create the owner")
    runtime.drive_next_activation()

    database = SQLiteDatabase.for_project(project_path)
    owners = SQLiteProjectOwnerAgentRepository()
    with database.transaction() as connection:
        owner = owners.get_by_triage_id(connection, activation.triage_id)
        assert owner is not None
        owners.update(connection, replace(owner, tools=("removed_tool",)))

    restarted = ProjectRuntime(
        project_path=project_path,
        settings=settings,
        approval_mode="yolo",
        responses_transport=_transport(settings),
    )
    with pytest.raises(ValueError, match="Unknown tool: 'removed_tool'"):
        restarted.submit_message("continue")


def test_restart_restores_a_published_dual_summary_before_new_raw_messages(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    settings = _settings(capacity_tokens=1_500)
    first_transport = _transport(settings)
    runtime = ProjectRuntime(
        project_path=project_path,
        settings=settings,
        approval_mode="yolo",
        responses_transport=first_transport,
    )
    runtime.submit_message("long-history " * 600)
    first = runtime.drive_next_activation()
    assert first.activation is not None
    assert first.activation.summary_id is not None

    restarted_transport = _transport(settings)
    restarted = ProjectRuntime(
        project_path=project_path,
        settings=settings,
        approval_mode="yolo",
        responses_transport=restarted_transport,
    )
    restarted.submit_message("new raw message")
    driven = restarted.drive_next_activation()

    assert driven.exit is not None
    owner_request = next(
        request
        for request in restarted_transport.requests
        if request.tool_choice == "auto"
    )
    assert [message.get("role") for message in owner_request.input] == [
        "developer",
        "user",
        "assistant",
        "user",
    ]
    summary_parts = owner_request.input[1]["content"]
    assert isinstance(summary_parts, list)
    assert summary_parts[0]["text"].startswith("<intent-summary>")
    assert summary_parts[1]["text"].startswith("<trajectory-summary>")
    assert owner_request.input[-1]["content"] == "new raw message"


def test_attribution_resolves_the_latest_summary_not_after_its_checkpoint(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    settings = _settings(capacity_tokens=1_500)
    transport = _transport(settings)
    runtime = ProjectRuntime(
        project_path=project_path,
        settings=settings,
        approval_mode="yolo",
        responses_transport=transport,
    )

    first_activation = runtime.submit_message("first-history " * 600)
    first = runtime.drive_next_activation()
    checkpoint = runtime.submit_message("historical checkpoint")
    runtime.drive_next_activation()
    runtime.submit_message("future-history " * 600)
    future = runtime.drive_next_activation()

    assert first.activation is not None
    assert first.activation.summary_id is not None
    assert future.activation is not None
    assert future.activation.summary_id is not None
    assert future.activation.summary_id != first.activation.summary_id

    contexts = ProjectOwnerContextQuery(
        SQLiteDatabase.for_project(project_path),
        settings.runtime.prompts.summary_context_header,
    )
    selected_summary_id = contexts.latest_summary_id_through(checkpoint.message_id)

    assert selected_summary_id == first.activation.summary_id
    raw = contexts.restore(checkpoint.message_id)
    restored = contexts.restore(
        checkpoint.message_id,
        summary_id=selected_summary_id,
    )
    assert raw.summary_id is None
    assert restored.summary_id == first.activation.summary_id
    assert restored.messages[1]["role"] == "developer"
    assert restored.messages[2]["role"] == "user"
    assert restored.messages[-1]["content"] == "historical checkpoint"
    with pytest.raises(ValueError, match="must not follow activation message"):
        contexts.restore(
            checkpoint.message_id,
            summary_id=future.activation.summary_id,
        )

    exact = contexts.restore(
        first_activation.message_id,
        summary_id=first.activation.summary_id,
    )
    assert len(exact.messages) == 3
