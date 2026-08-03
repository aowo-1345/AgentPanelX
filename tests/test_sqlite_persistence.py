"""Observable SQLite persistence behavior."""

import shutil
import subprocess
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from agentplanex.domains import (
    MessageHistory,
    ProjectOwnerAgent,
    ProjectRuntimeContext,
    SummaryHistory,
)
from agentplanex.infrastructure.sqlite import SQLiteDatabase, initialize_schema
from agentplanex.infrastructure.sqlite.repositories import (
    SQLiteMessageHistoryRepository,
    SQLiteProjectOwnerAgentRepository,
    SQLiteProjectRuntimeContextRepository,
    SQLiteSummaryHistoryRepository,
)


@pytest.fixture
def project_path(request: pytest.FixtureRequest) -> Iterator[Path]:
    directory = (
        Path(__file__).resolve().parent.parent
        / ".agentplanex"
        / "tests"
        / request.node.name
    )
    shutil.rmtree(directory, ignore_errors=True)
    directory.mkdir(parents=True)
    yield directory
    shutil.rmtree(directory, ignore_errors=True)


def test_context_can_be_reloaded_and_assembled(project_path: Path) -> None:
    database = SQLiteDatabase.for_project(project_path)
    runtimes = SQLiteProjectRuntimeContextRepository()
    owners = SQLiteProjectOwnerAgentRepository()
    summaries = SQLiteSummaryHistoryRepository()
    messages = SQLiteMessageHistoryRepository()
    initialize_schema(database)

    summary = SummaryHistory("session-1", "summary-1", "The project is being refactored.")
    message_history = MessageHistory(
        "session-1",
        "message-1",
        1,
        (
            {"role": "user", "content": "Continue the refactor."},
            {"role": "assistant", "content": "I will inspect the current state."},
        ),
    )
    owner = ProjectOwnerAgent(
        triage_id="triage-1",
        project_owner_session_id="session-1",
        system_prompt="Own the project.",
        tools=("bash",),
        summary_id=summary.summary_id,
        message_id=message_history.message_id,
    )
    runtime = ProjectRuntimeContext(
        triage_id="triage-1",
        status="running",
        git_main_version="main",
    )

    with database.transaction() as connection:
        runtimes.insert(connection, runtime)
        summaries.insert(connection, summary)
        messages.insert(connection, message_history)
        owners.insert(connection, owner)

    initialize_schema(database)
    with database.connection() as connection:
        loaded_runtime = runtimes.get(connection, "triage-1")
        loaded_owner = owners.get_by_triage_id(connection, "triage-1")
        assert loaded_runtime is not None
        assert loaded_owner is not None
        assert loaded_owner.summary_id is not None
        assert loaded_owner.message_id is not None
        loaded_summary = summaries.get(connection, loaded_owner.summary_id)
        loaded_messages = messages.get(connection, loaded_owner.message_id)

    assembled_owner = replace(
        loaded_owner,
        summary_history=loaded_summary,
        message_history=loaded_messages,
    )
    assembled_runtime = replace(
        loaded_runtime,
        project_owner_agent=assembled_owner,
    )

    assert assembled_runtime.project_owner_agent is not None
    assert assembled_runtime.project_owner_agent.summary_history == summary
    assert assembled_runtime.project_owner_agent.message_history == message_history


def test_failed_transaction_does_not_leave_partial_state(
    project_path: Path,
) -> None:
    database = SQLiteDatabase.for_project(project_path)
    runtimes = SQLiteProjectRuntimeContextRepository()
    initialize_schema(database)

    with (
        pytest.raises(RuntimeError, match="stop the transaction"),
        database.transaction() as connection,
    ):
        runtimes.insert(
            connection,
            ProjectRuntimeContext(
                triage_id="triage-rollback",
                status="running",
                git_main_version="main",
            ),
        )
        raise RuntimeError("stop the transaction")

    with database.connection() as connection:
        assert runtimes.get(connection, "triage-rollback") is None


def test_git_project_fixture_initializes_project_database(
    initialize_git_project: Callable[[], Path],
) -> None:
    fixture_project = initialize_git_project()
    database = SQLiteDatabase.for_project(fixture_project)

    assert database.path == fixture_project / ".agentplanex" / "agentplanex.sqlite3"
    assert database.path.is_file()
    with database.connection() as connection:
        schema_version = connection.execute("PRAGMA user_version").fetchone()
    assert schema_version is not None
    assert schema_version[0] == 2

    git_status = subprocess.run(
        ["git", "-C", str(fixture_project), "status", "--short"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert git_status.stdout == ""
