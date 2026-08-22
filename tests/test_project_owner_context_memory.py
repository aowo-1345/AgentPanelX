"""Critical Project Owner context-memory journeys and integrity boundaries."""

import json
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Lock

import pytest

from agentplanex.bootstrap import (
    create_project_control_query,
    create_project_runtime_control,
    create_project_workspace_query,
)
from agentplanex.infrastructure.sqlite import SQLiteDatabase
from agentplanex.infrastructure.sqlite.repositories import (
    SQLiteProjectOwnerAgentRepository,
    SQLiteSummaryHistoryRepository,
)
from agentplanex.project_owner_agent.context.models import SummaryHistory
from agentplanex.project_owner_agent.models.responses import (
    ResponsesRequest,
    ResponsesTransport,
)
from agentplanex.services.agent_invocation import AgentPromptCatalog
from agentplanex.services.historical_owner import HistoricalOwnerForkService
from agentplanex.settings import (
    DEFAULT_SETTINGS_PATH,
    ContextMemorySettings,
    Settings,
    load_settings,
)


class _OwnerTransport(ResponsesTransport):
    """Replace only the remote model while recording complete provider requests."""

    def __init__(
        self,
        settings: Settings,
        *,
        first_owner_tool: bool = False,
        summary_failure: str | None = None,
    ) -> None:
        prompts = settings.runtime.prompts
        self.trajectory_prompt = prompts.trajectory_summary.strip()
        self.initial_intent_prompt = prompts.initial_intent_summary.strip()
        self.update_intent_prompt = prompts.update_intent_summary.strip()
        self.first_owner_tool = first_owner_tool
        self.summary_failure = summary_failure
        self.requests: list[ResponsesRequest] = []
        self.owner_request_count = 0
        self._lock = Lock()

    def create(self, request: ResponsesRequest) -> object:
        with self._lock:
            self.requests.append(request)
        task = str(request.input[-1].get("content", ""))
        if task == self.trajectory_prompt:
            if self.summary_failure == "gateway":
                raise RuntimeError("trajectory unavailable")
            if self.summary_failure == "tool":
                return _tool_response("bash", "summary-tool", {"command": "pwd"})
            return _text_response(
                "<trajectory-summary>Continue from the recorded project work.</trajectory-summary>"
            )
        if task == self.initial_intent_prompt:
            if self.summary_failure == "xml":
                return _text_response("invalid summary response")
            return _text_response(
                "<intent-summary>Deliver durable context memory.</intent-summary>"
            )
        if task == self.update_intent_prompt:
            return _text_response(
                "<intent-summary>Deliver durable context memory after restart.</intent-summary>"
            )

        with self._lock:
            owner_index = self.owner_request_count
            self.owner_request_count += 1
        if self.first_owner_tool and owner_index == 0:
            return _tool_response(
                "bash",
                "large-observation",
                {"command": ("for i in $(seq 1 500); do printf 'observation '; done")},
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
        ],
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
        ],
    }


# Leave room for the static Owner contract so seeded history triggers compaction.
def _settings(capacity_tokens: int = 4_000) -> Settings:
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


def test_workspace_conversation_surfaces_live_project_owner_tool_activity(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    settings = _settings(capacity_tokens=128_000)
    runtime = create_project_runtime_control(
        project_path=project_path,
        settings=settings,
        approval_mode="yolo",
        responses_transport=_OwnerTransport(settings),
    )
    runtime.initialize()

    activation = runtime.submit_message("Inspect the project before replying")
    release = project_path / "release-tool"
    command = (
        "OPENAI_API_KEY=input-secret; "
        "while [ ! -f release-tool ]; do sleep 0.02; done; printf completed; # "
        + "x" * 2_000
    )
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            runtime.drive_owner_tool,
            {
                "tool": "bash",
                "call_id": "running-tool",
                "arguments": {"command": command},
            },
        )
        try:
            deadline = time.monotonic() + 5
            while True:
                conversation = create_project_workspace_query(
                    project_path=project_path
                ).get(activation.triage_id).conversation
                running = next(
                    (message for message in conversation if message.role == "tool"),
                    None,
                )
                if running is not None:
                    break
                if time.monotonic() >= deadline:
                    pytest.fail("Running tool activity did not become visible")
                time.sleep(0.02)

            assert running.tool_activity is not None
            assert running.tool_activity.name == "bash"
            assert running.tool_activity.status == "running"
            assert "input-secret" not in running.tool_activity.input_preview
            assert "[redacted]" in running.tool_activity.input_preview
            assert len(running.tool_activity.input_preview) <= 1_200
            assert running.tool_activity.output_preview is None
        finally:
            release.touch()
        future.result(timeout=5)

    completed = next(
        message
        for message in create_project_workspace_query(project_path=project_path)
        .get(activation.triage_id)
        .conversation
        if message.role == "tool"
    )
    assert completed.message_id == running.message_id
    assert completed.tool_activity is not None
    assert completed.tool_activity.status == "completed"
    assert "completed" in (completed.tool_activity.output_preview or "")

    runtime.drive_owner_tool(
        {
            "tool": "bash",
            "call_id": "failed-tool",
            "arguments": {
                "command": (
                    "printf '%s\\n' 'OPENAI_API_KEY=redacted-output-token-123456' "
                    "'https://user:pass@example.com' "
                    "'ghp_abcdefghijklmnopqrstuvwxyz123456' "
                    "'glpat-abcdefghijklmnopqrst' "
                    "'xox"
                    "b-redaction-fixture-1234567890' "
                    "'AIzaabcdefghijklmnopqrstuvwxyz123456' "
                    "'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0."
                    "signaturevalue123456' >&2; exit 7"
                ),
            },
        }
    )
    failed = [
        message
        for message in create_project_workspace_query(project_path=project_path)
        .get(activation.triage_id)
        .conversation
        if message.role == "tool"
    ][-1]
    assert failed.tool_activity is not None
    assert failed.tool_activity.status == "failed"
    assert "output-secret" not in failed.tool_activity.input_preview
    assert "output-secret" not in (failed.tool_activity.output_preview or "")
    for secret_marker in ("user:pass", "ghp_", "glpat-", "xoxb-", "AIza", "eyJ"):
        assert secret_marker not in failed.tool_activity.input_preview
        assert secret_marker not in (failed.tool_activity.output_preview or "")
    assert "[redacted]" in (failed.tool_activity.output_preview or "")


def test_workspace_conversation_projects_model_tool_as_one_completed_activity(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    settings = _settings(capacity_tokens=128_000)
    runtime = create_project_runtime_control(
        project_path=project_path,
        settings=settings,
        approval_mode="yolo",
        responses_transport=_OwnerTransport(settings, first_owner_tool=True),
    )
    runtime.initialize()

    activation = runtime.submit_message("Inspect the project before replying")
    driven = runtime.drive_owner_model()

    assert driven.exit is not None
    assert driven.exit.content == "owner-finished"
    conversation = create_project_workspace_query(project_path=project_path).get(
        activation.triage_id
    ).conversation
    assert [message.role for message in conversation] == ["user", "tool", "assistant"]
    activity = conversation[1].tool_activity
    assert activity is not None
    assert activity.name == "bash"
    assert activity.status == "completed"
    assert "for i in" in activity.input_preview
    assert "observation" in (activity.output_preview or "")
    assert conversation[2].content == "owner-finished"


def test_context_memory_crosses_the_threshold_via_bash_and_survives_restart(
    initialize_git_project: Callable[[], Path],
) -> None:
    """One user journey proves the complete Issue 01/02 happy path."""

    project_path = initialize_git_project()
    # The provider-compatible inlined Tool schema is larger than Pydantic's
    # reference-based form. Keep this journey's threshold above the initial
    # request but below the large Bash observation it is intended to exercise.
    settings = _settings(capacity_tokens=3_400)
    first_transport = _OwnerTransport(settings, first_owner_tool=True)
    runtime = create_project_runtime_control(
        project_path=project_path,
        settings=settings,
        approval_mode="yolo",
        responses_transport=first_transport,
    )
    runtime.initialize()

    first_activation = runtime.submit_message("Inspect the project before replying")
    first = runtime.drive_owner_model()

    assert first.exit is not None
    assert first.exit.content == "owner-finished"
    assert [request.tool_choice for request in first_transport.requests] == [
        "auto",
        "none",
        "none",
        "auto",
    ]
    first_summary_requests = [
        request for request in first_transport.requests if request.tool_choice == "none"
    ]
    first_owner_request = first_transport.requests[0]
    first_owner_affinity = first_owner_request.cache_affinity_key
    first_summary_affinity = first_summary_requests[0].cache_affinity_key
    assert first_owner_affinity is not None
    assert first_summary_affinity is not None
    assert first_owner_affinity != first_summary_affinity
    assert all(
        request.cache_affinity_key == first_summary_affinity
        for request in first_summary_requests
    )
    assert all(
        request.input[:-1] == first_summary_requests[0].input[:-1]
        for request in first_summary_requests
    )
    assert all(request.tools == first_owner_request.tools for request in first_summary_requests)
    assert {str(request.input[-1].get("content", "")) for request in first_summary_requests} == {
        first_transport.trajectory_prompt,
        first_transport.initial_intent_prompt,
    }
    bash_history = json.dumps(
        first_summary_requests[0].input,
        ensure_ascii=False,
    )
    assert "function_call_output" in bash_history
    assert "observation observation" in bash_history

    database = SQLiteDatabase.for_project(project_path)
    owners = SQLiteProjectOwnerAgentRepository()
    summaries = SQLiteSummaryHistoryRepository()
    with database.transaction() as connection:
        owner = owners.get_by_triage_id(connection, first_activation.triage_id)
        assert owner is not None
        assert owner.summary_id is not None
        first_summary_id = owner.summary_id
        owners.update(
            connection,
            replace(
                owner,
                system_prompt="Persisted Owner identity.",
                tools=("bash", "talk_to_agent"),
            ),
        )

    configured_prompts = settings.runtime.prompts
    changed_settings = settings.model_copy(
        update={
            "project_owner_agent": settings.project_owner_agent.model_copy(
                update={
                    "context_memory": ContextMemorySettings(
                        capacity_tokens=2_500,
                        compaction_threshold=0.8,
                    )
                }
            ),
            "runtime": settings.runtime.model_copy(
                update={
                    "prompts": configured_prompts.model_copy(
                        update={
                            "project_owner": (
                                configured_prompts.project_owner.model_copy(
                                    update={"role": "A changed configured identity."}
                                )
                            )
                        }
                    )
                }
            ),
        }
    )
    restarted_transport = _OwnerTransport(changed_settings)
    restarted = create_project_runtime_control(
        project_path=project_path,
        settings=changed_settings,
        approval_mode="yolo",
        responses_transport=restarted_transport,
    )
    second_activation = restarted.submit_message("updated-context " * 600)
    second = restarted.drive_owner_model()

    assert second.exit is not None
    assert second.exit.content == "owner-finished"
    second_summary_requests = [
        request for request in restarted_transport.requests if request.tool_choice == "none"
    ]
    second_owner_request = next(
        request for request in restarted_transport.requests if request.tool_choice == "auto"
    )
    assert second_owner_request.cache_affinity_key == first_owner_affinity
    assert all(
        request.cache_affinity_key == first_summary_affinity
        for request in second_summary_requests
    )
    assert len(second_summary_requests) == 2
    assert all(
        request.input[:-1] == second_summary_requests[0].input[:-1]
        for request in second_summary_requests
    )
    assert all(
        [schema["name"] for schema in request.tools] == ["bash", "talk_to_agent"]
        for request in restarted_transport.requests
    )
    assert all(
        request.instructions == "Persisted Owner identity."
        for request in restarted_transport.requests
    )
    assert all(
        "AgentPlaneX invocation envelope" not in request.instructions
        for request in [*first_transport.requests, *restarted_transport.requests]
    )
    restored_prefix = json.dumps(
        second_summary_requests[0].input[:-1],
        ensure_ascii=False,
    )
    assert [message.get("role") for message in second_summary_requests[0].input[:-1]] == [
        "developer",
        "user",
        "assistant",
        "user",
        "developer",
    ]
    assert "<intent-summary>" in restored_prefix
    assert "<trajectory-summary>" in restored_prefix
    assert "updated-context" in restored_prefix
    tasks = {str(request.input[-1].get("content", "")) for request in second_summary_requests}
    assert tasks == {
        restarted_transport.trajectory_prompt,
        restarted_transport.update_intent_prompt,
    }
    assert [message.get("role") for message in second_owner_request.input] == [
        "developer",
        "user",
        "developer",
    ]

    with database.read_only_connection() as connection:
        owner = owners.get_by_triage_id(connection, first_activation.triage_id)
        assert owner is not None
        assert owner.summary_id is not None
        second_summary = summaries.get(connection, owner.summary_id)
    assert owner.summary_id != first_summary_id
    assert second_summary is not None
    assert second_summary.covered_through_message_id == second_activation.message_id
    assert second_summary.intent_summary_content == (
        "Deliver durable context memory after restart."
    )
    assert first_summary_id not in json.dumps(second_owner_request.input)
    assert owner.summary_id not in json.dumps(second_owner_request.input)

    completed = [
        event
        for event in create_project_control_query(project_path=project_path).get_current().timeline
        if event.event_type.value == "CONTEXT_COMPACTION_COMPLETED"
    ]
    assert [event.payload["query_index"] for event in completed] == [1, 0]


@pytest.mark.parametrize("summary_failure", ["gateway", "tool", "xml"])
def test_summary_failure_keeps_the_original_owner_context(
    initialize_git_project: Callable[[], Path],
    summary_failure: str,
) -> None:
    """Transport, forbidden-tool and invalid-output failures share one fallback."""

    project_path = initialize_git_project()
    settings = _settings()
    transport = _OwnerTransport(settings, summary_failure=summary_failure)
    runtime = create_project_runtime_control(
        project_path=project_path,
        settings=settings,
        approval_mode="yolo",
        responses_transport=transport,
    )
    runtime.initialize()

    activation = runtime.submit_message("original-context " * 600)
    driven = runtime.drive_owner_model()

    assert driven.exit is not None
    assert driven.exit.content == "owner-finished"
    owner_request = next(request for request in transport.requests if request.tool_choice == "auto")
    assert str(owner_request.input[-2].get("content", "")).startswith("original-context")
    assert owner_request.input[-1].get("role") == "developer"
    database = SQLiteDatabase.for_project(project_path)
    owners = SQLiteProjectOwnerAgentRepository()
    with database.read_only_connection() as connection:
        owner = owners.get_by_triage_id(connection, activation.triage_id)
        summary_count = connection.execute("SELECT COUNT(*) FROM summary_history").fetchone()[0]
    assert owner is not None
    assert owner.summary_id is None
    assert summary_count == 0
    assert [
        event.event_type.value
        for event in create_project_control_query(project_path=project_path)
        .get_current()
        .timeline
    ].count(
        "CONTEXT_COMPACTION_FAILED"
    ) == 1


def test_summary_publish_transaction_rejects_a_stale_checkpoint(
    initialize_git_project: Callable[[], Path],
) -> None:
    """Exercise the real conditional UPDATE and transaction rollback directly."""

    project_path = initialize_git_project()
    settings = _settings(capacity_tokens=128_000)
    runtime = create_project_runtime_control(
        project_path=project_path,
        settings=settings,
        approval_mode="yolo",
        responses_transport=_OwnerTransport(settings),
    )
    runtime.initialize()
    activation = runtime.submit_message("create the owner checkpoint")
    runtime.drive_owner_model()

    database = SQLiteDatabase.for_project(project_path)
    owners = SQLiteProjectOwnerAgentRepository()
    summaries = SQLiteSummaryHistoryRepository()
    with (
        pytest.raises(RuntimeError, match="context changed during compaction"),
        database.transaction() as connection,
    ):
        owner = owners.get_by_triage_id(connection, activation.triage_id)
        assert owner is not None
        assert owner.message_id is not None
        summary = SummaryHistory(
            project_owner_session_id=owner.project_owner_session_id,
            summary_id="stale-summary",
            covered_through_message_id=owner.message_id,
            intent_summary_content="Keep the user intent.",
            trajectory_summary_content="Keep the project trajectory.",
        )
        summaries.insert(connection, summary)
        owners.advance_summary(
            connection,
            session_id=owner.project_owner_session_id,
            expected_message_id="superseded-message",
            expected_summary_id=None,
            summary_id=summary.summary_id,
        )

    with database.read_only_connection() as connection:
        owner = owners.get_by_triage_id(connection, activation.triage_id)
        stored_summary = summaries.get(connection, "stale-summary")
    assert owner is not None
    assert owner.summary_id is None
    assert stored_summary is None


def test_frozen_activation_cannot_replace_a_newer_summary_during_compaction(
    initialize_git_project: Callable[[], Path],
) -> None:
    """The Manager-to-Runtime seam preserves the Activation's Summary CAS."""

    project_path = initialize_git_project()
    roomy_settings = _settings(capacity_tokens=128_000)
    runtime = create_project_runtime_control(
        project_path=project_path,
        settings=roomy_settings,
        approval_mode="yolo",
        responses_transport=_OwnerTransport(roomy_settings),
    )
    runtime.initialize()
    baseline = runtime.submit_message("establish a stable history checkpoint")
    runtime.drive_owner_model()

    database = SQLiteDatabase.for_project(project_path)
    owners = SQLiteProjectOwnerAgentRepository()
    summaries = SQLiteSummaryHistoryRepository()
    with database.transaction() as connection:
        owner = owners.get_by_triage_id(connection, baseline.triage_id)
        assert owner is not None
        assert owner.message_id is not None
        frozen_summary = SummaryHistory(
            project_owner_session_id=owner.project_owner_session_id,
            summary_id="frozen-summary",
            covered_through_message_id=owner.message_id,
            intent_summary_content="Preserve the frozen intent.",
            trajectory_summary_content="Preserve the frozen trajectory.",
        )
        summaries.insert(connection, frozen_summary)
        owners.update(
            connection,
            replace(owner, summary_id=frozen_summary.summary_id),
        )

    activation = runtime.submit_message("frozen-activation-context " * 600)
    assert activation.summary_id == frozen_summary.summary_id

    with database.transaction() as connection:
        owner = owners.get_by_triage_id(connection, activation.triage_id)
        assert owner is not None
        assert owner.message_id == activation.message_id
        newer_summary = SummaryHistory(
            project_owner_session_id=owner.project_owner_session_id,
            summary_id="newer-summary",
            covered_through_message_id=activation.message_id,
            intent_summary_content="A newer intent already won.",
            trajectory_summary_content="A newer trajectory already won.",
        )
        summaries.insert(connection, newer_summary)
        owners.update(
            connection,
            replace(owner, summary_id=newer_summary.summary_id),
        )
        summary_count_before = connection.execute(
            "SELECT COUNT(*) FROM summary_history"
        ).fetchone()[0]

    compact_settings = _settings(capacity_tokens=3_000)
    transport = _OwnerTransport(compact_settings)
    restarted = create_project_runtime_control(
        project_path=project_path,
        settings=compact_settings,
        approval_mode="yolo",
        responses_transport=transport,
    )
    driven = restarted.drive_owner_model()

    assert driven.activation is not None
    assert driven.activation.activation_id == activation.activation_id
    assert driven.exit is not None
    assert driven.exit.content == "owner-finished"
    owner_request = next(
        request for request in transport.requests if request.tool_choice == "auto"
    )
    assert "frozen-activation-context" in json.dumps(
        owner_request.input,
        ensure_ascii=False,
    )
    with database.read_only_connection() as connection:
        owner = owners.get_by_triage_id(connection, activation.triage_id)
        summary_count_after = connection.execute(
            "SELECT COUNT(*) FROM summary_history"
        ).fetchone()[0]
    assert owner is not None
    assert owner.summary_id == newer_summary.summary_id
    assert summary_count_after == summary_count_before
    compaction_events = [
        event.event_type.value
        for event in create_project_control_query(project_path=project_path).get_current().timeline
        if event.payload.get("activation_id") == activation.activation_id
    ]
    assert compaction_events.count("CONTEXT_COMPACTION_STARTED") == 1
    assert compaction_events.count("CONTEXT_COMPACTION_FAILED") == 1
    assert "CONTEXT_COMPACTION_COMPLETED" not in compaction_events


def test_restart_fails_when_a_persisted_owner_tool_is_missing(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    settings = _settings(capacity_tokens=128_000)
    runtime = create_project_runtime_control(
        project_path=project_path,
        settings=settings,
        approval_mode="yolo",
        responses_transport=_OwnerTransport(settings),
    )
    runtime.initialize()
    activation = runtime.submit_message("create the owner")
    runtime.drive_owner_model()

    database = SQLiteDatabase.for_project(project_path)
    owners = SQLiteProjectOwnerAgentRepository()
    with database.transaction() as connection:
        owner = owners.get_by_triage_id(connection, activation.triage_id)
        assert owner is not None
        owners.update(connection, replace(owner, tools=("removed_tool",)))

    restarted = create_project_runtime_control(
        project_path=project_path,
        settings=settings,
        approval_mode="yolo",
        responses_transport=_OwnerTransport(settings),
    )
    with pytest.raises(ValueError, match="Unknown tool: 'removed_tool'"):
        restarted.submit_message("continue")


def test_attribution_uses_the_summary_available_at_its_checkpoint(
    initialize_git_project: Callable[[], Path],
) -> None:
    project_path = initialize_git_project()
    settings = _settings(capacity_tokens=3_000)
    runtime = create_project_runtime_control(
        project_path=project_path,
        settings=settings,
        approval_mode="yolo",
        responses_transport=_OwnerTransport(settings),
    )
    runtime.initialize()

    runtime.submit_message("first-history " * 600)
    first = runtime.drive_owner_model()
    checkpoint = runtime.submit_message("historical checkpoint")
    at_checkpoint = runtime.drive_owner_model()
    runtime.submit_message("future-history " * 600)
    future = runtime.drive_owner_model()

    assert first.activation is not None
    assert first.activation.summary_id is not None
    assert at_checkpoint.activation is not None
    assert at_checkpoint.activation.summary_id is not None
    assert future.activation is not None
    assert future.activation.summary_id is not None
    database = SQLiteDatabase.for_project(project_path)
    owners = SQLiteProjectOwnerAgentRepository()
    contexts = HistoricalOwnerForkService(
        database,
        AgentPromptCatalog(settings.runtime.prompts),
    )
    selected_summary_id = contexts.latest_summary_id_through(checkpoint.message_id)

    assert selected_summary_id == at_checkpoint.activation.summary_id
    assert contexts.restore(checkpoint.message_id).summary is None
    restored = contexts.restore(
        checkpoint.message_id,
        summary_id=selected_summary_id,
    )
    assert restored.summary is not None
    assert restored.summary.summary_id == at_checkpoint.activation.summary_id
    assert restored.summary.covered_through_message_id == checkpoint.message_id
    assert restored.message_history == ()
    with pytest.raises(ValueError, match="must not follow activation message"):
        contexts.restore(
            checkpoint.message_id,
            summary_id=future.activation.summary_id,
        )

    with database.transaction() as connection:
        owner = owners.get_by_triage_id(connection, checkpoint.triage_id)
        assert owner is not None
        owners.update(
            connection,
            replace(owner, message_id=checkpoint.message_id),
        )

    restored_with_stale_live_pointer = contexts.restore(
        checkpoint.message_id,
        summary_id=selected_summary_id,
    )
    assert restored_with_stale_live_pointer.through_message_id == checkpoint.message_id
