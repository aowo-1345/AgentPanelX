"""Observable tests for the isolated Historical Project Owner Fork CLI."""

import json
import subprocess
from collections.abc import Callable, Iterator
from dataclasses import replace
from io import StringIO
from pathlib import Path
from typing import ClassVar

import pytest

from agentplanex.domains import ActionOutput, Message, SummaryHistory
from agentplanex.infrastructure.sqlite import SQLiteDatabase
from agentplanex.infrastructure.sqlite.repositories import (
    SQLiteMessageHistoryRepository,
    SQLiteProjectOwnerAgentRepository,
    SQLiteProjectRuntimeContextRepository,
    SQLiteSummaryHistoryRepository,
)
from agentplanex.project_owner_agent.exception import ReplyToHuman
from agentplanex.project_owner_agent.models import jbb as jbb_model_module
from agentplanex.project_owner_agent.models.jbb import JBBModel
from agentplanex.settings import DEFAULT_SETTINGS_PATH, load_settings
from scripts import debug_owner_fork_cli, debug_tool_cli


class _WitnessModel:
    queries: ClassVar[list[list[Message]]] = []
    constructions: ClassVar[list[dict[str, object]]] = []

    def __init__(self, **kwargs: object) -> None:
        assert kwargs["tools"] is None
        type(self).constructions.append(kwargs)

    def query(self, messages: list[Message]) -> Message:
        type(self).queries.append([dict(message) for message in messages])
        question = str(messages[-1].get("content", ""))
        answer = f"historical-witness: {question}"
        raise ReplyToHuman(
            content=answer,
            response={"role": "assistant", "content": answer},
        )

    def format_observation_messages(
        self,
        message: Message,
        outputs: list[ActionOutput],
    ) -> list[Message]:
        raise AssertionError("Historical Owner Fork cannot execute tools")


@pytest.fixture
def historical_project(
    initialize_git_project: Callable[[], Path],
    capfd: pytest.CaptureFixture[str],
) -> Iterator[tuple[Path, str, SummaryHistory]]:
    project_path = initialize_git_project()
    assert debug_tool_cli.main(
        ["--cwd", str(project_path), "--print", "message", "first"]
    ) == 0
    capfd.readouterr()
    assert debug_tool_cli.main(
        ["--cwd", str(project_path), "--print", "drive", "reply", "first-reply"]
    ) == 0
    capfd.readouterr()
    assert debug_tool_cli.main(
        ["--cwd", str(project_path), "--print", "message", "second"]
    ) == 0
    second = json.loads(capfd.readouterr().out)
    through_message_id = second["activation"]["message_id"]
    assert isinstance(through_message_id, str)
    assert debug_tool_cli.main(
        ["--cwd", str(project_path), "--print", "drive", "reply", "second-reply"]
    ) == 0
    capfd.readouterr()

    database = SQLiteDatabase.for_project(project_path)
    owners = SQLiteProjectOwnerAgentRepository()
    messages = SQLiteMessageHistoryRepository()
    summaries = SQLiteSummaryHistoryRepository()
    with database.transaction() as connection:
        contexts = SQLiteProjectRuntimeContextRepository().list_all(connection)
        assert len(contexts) == 1
        owner = owners.get_by_triage_id(connection, contexts[0].triage_id)
        assert owner is not None
        histories = messages.list_by_session_id(
            connection,
            owner.project_owner_session_id,
        )
        summary = SummaryHistory(
            project_owner_session_id=owner.project_owner_session_id,
            summary_id="summary-owner-fork",
            covered_through_message_id=histories[1].message_id,
            intent_summary_content="Continue the requested work.",
            trajectory_summary_content="The first exchange was handled.",
        )
        summaries.insert(connection, summary)
        owners.update(connection, replace(owner, summary_id=summary.summary_id))

    yield project_path, through_message_id, summary


def test_owner_fork_cli_prints_raw_and_summary_context_without_runtime_writes(
    historical_project: tuple[Path, str, SummaryHistory],
    capfd: pytest.CaptureFixture[str],
) -> None:
    project_path, through_message_id, summary = historical_project
    before = _database_snapshot(project_path)

    raw_code = debug_owner_fork_cli.main(
        [
            "--cwd",
            str(project_path),
            "--message-id",
            through_message_id,
            "--print-context",
        ]
    )
    raw = json.loads(capfd.readouterr().out)
    projected_code = debug_owner_fork_cli.main(
        [
            "--cwd",
            str(project_path),
            "--message-id",
            through_message_id,
            "--summary-id",
            summary.summary_id,
            "--print-context",
        ]
    )
    projected = json.loads(capfd.readouterr().out)

    assert raw_code == 0
    assert raw["context"]["summary"] is None
    assert [message.get("content") for message in raw["context"]["messages"]] == [
        load_settings(DEFAULT_SETTINGS_PATH).runtime.prompts.project_owner.role.strip(),
        "first",
        "first-reply",
        "second",
    ]
    assert projected_code == 0
    assert projected["context"]["summary"]["summary_id"] == summary.summary_id
    assert projected["context"]["summary"]["intent_summary_content"] == (
        "Continue the requested work."
    )
    assert projected["context"]["summary"]["trajectory_summary_content"] == (
        "The first exchange was handled."
    )
    assert [
        message.get("content") for message in projected["context"]["messages"]
    ][-1] == "second"
    _assert_unchanged(project_path, before)


def test_owner_fork_cli_keeps_two_interrogation_turns_in_memory_only(
    historical_project: tuple[Path, str, SummaryHistory],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_path, through_message_id, summary = historical_project
    before = _database_snapshot(project_path)
    _WitnessModel.queries = []
    _WitnessModel.constructions = []
    monkeypatch.setattr(debug_owner_fork_cli, "JBBModel", _WitnessModel)
    questions = iter(
        (
            "What state did you believe the project was in?",
            "Which fact would have changed your decision?",
            "/exit",
        )
    )
    output = StringIO()

    code = debug_owner_fork_cli.main(
        [
            "--cwd",
            str(project_path),
            "--message-id",
            through_message_id,
            "--summary-id",
            summary.summary_id,
        ],
        read_input=lambda _prompt: next(questions),
        stdout=output,
    )

    lines = [json.loads(line) for line in output.getvalue().splitlines()]
    assert code == 0
    assert lines[0]["action"] == "fork-opened"
    assert lines[0]["fidelity"] == {
        "message_checkpoint": "EXACT",
        "summary_selection": "EXACT",
        "agent_definition": "CURRENT_PERSISTED",
        "model": "CURRENT_CONFIG_NEW_INVOCATION",
    }
    assert [line["turn"] for line in lines[1:]] == [1, 2]
    assert [line["answer"] for line in lines[1:]] == [
        "historical-witness: What state did you believe the project was in?",
        "historical-witness: Which fact would have changed your decision?",
    ]
    assert len(_WitnessModel.queries) == 2
    assert len(_WitnessModel.constructions) == 1
    configured_model = load_settings(DEFAULT_SETTINGS_PATH).project_owner_agent
    construction = _WitnessModel.constructions[0]
    assert construction["model"] == configured_model.selected_model.name
    transport = construction["transport"]
    assert isinstance(transport, jbb_model_module.OpenAIResponsesTransport)
    assert transport.base_url == configured_model.selected_model.base_url
    assert transport.api_key_env == configured_model.selected_model.api_key_env
    assert "Historical Project Owner Fork" in str(
        _WitnessModel.queries[0][0]["content"]
    )
    assert _WitnessModel.queries[0][-1]["content"] == lines[1]["question"]
    assert [message.get("content") for message in _WitnessModel.queries[1][-3:]] == [
        lines[1]["question"],
        lines[1]["answer"],
        lines[2]["question"],
    ]
    _assert_unchanged(project_path, before)


def test_debug_clis_do_not_import_each_other() -> None:
    tool_source = Path(debug_tool_cli.__file__).read_text(encoding="utf-8")
    fork_source = Path(debug_owner_fork_cli.__file__).read_text(encoding="utf-8")

    assert "debug_owner_fork_cli" not in tool_source
    assert "debug_tool_cli" not in fork_source


def test_jbb_model_omits_tool_surface_for_historical_fork(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[dict[str, object]] = []

    class _Responses:
        def create(self, **kwargs: object) -> dict[str, object]:
            requests.append(kwargs)
            return {
                "object": "response",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {"type": "output_text", "text": "checkpoint answer"}
                        ],
                    }
                ],
            }

    class _Client:
        responses = _Responses()

    monkeypatch.setattr(
        jbb_model_module,
        "OpenAI",
        lambda **_kwargs: _Client(),
    )
    model = JBBModel(model="owner-model", tools=None)

    with pytest.raises(ReplyToHuman, match="checkpoint answer"):
        model.query(
            [
                {"role": "system", "content": "Historical witness contract"},
                {"role": "user", "content": "What did you know?"},
            ]
        )

    assert len(requests) == 1
    assert "tools" not in requests[0]
    assert "tool_choice" not in requests[0]
    assert "parallel_tool_calls" not in requests[0]


def _database_snapshot(project_path: Path) -> dict[str, tuple[tuple[object, ...], ...]]:
    database = SQLiteDatabase.for_project(project_path)
    with database.read_only_connection() as connection:
        tables = tuple(
            row["name"]
            for row in connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                ORDER BY name
                """
            ).fetchall()
        )
        return {
            table: tuple(
                tuple(row)
                for row in connection.execute(
                    f'SELECT * FROM "{table}" ORDER BY rowid'
                ).fetchall()
            )
            for table in tables
        }


def _assert_unchanged(
    project_path: Path,
    before: dict[str, tuple[tuple[object, ...], ...]],
) -> None:
    assert _database_snapshot(project_path) == before
    status = subprocess.run(
        ["git", "-C", str(project_path), "status", "--short"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert status.stdout == ""
