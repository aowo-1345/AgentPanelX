"""Attribution history exposed through the public Workspace projection."""

from dataclasses import asdict
from pathlib import Path

from agentplanex.domains.project_runtime_state import ProjectRuntimeState
from agentplanex.domains.workspace import FeatureBinding, ManagedProject
from agentplanex.infrastructure.agent_workspace import AgentWorkspaceStore
from agentplanex.infrastructure.git_repository import GitRepository
from agentplanex.infrastructure.sqlite import SQLiteDatabase, initialize_schema
from agentplanex.infrastructure.sqlite.repositories import (
    SQLiteAutoTakeoverRepository,
    SQLiteProjectRuntimeStateRepository,
)
from agentplanex.services.auto_takeover.models import TakeoverStatus
from agentplanex.services.web import ProjectWorkspaceQuery
from agentplanex.services.web.to_issue import CreatedIssue, ProposalToIssue
from agentplanex.services.workspace.queries import FeatureWorkspaceView
from agentplanex.web.schemas import workspace_response


def _publish_report(
    store: AgentWorkspaceStore,
    *,
    request_key: str,
    content: str,
):
    workspace = store.get_or_create_managed(
        agent_id="auto_takeover",
        profile_digest="a" * 64,
        session_key="feature-attribution",
    )
    managed = store.prepare_managed_invocation(
        workspace,
        request_key=request_key,
        request_digest=f"digest-{request_key}",
    )
    document = store.execution_path(workspace) / "documents" / "attribution.md"
    document.write_text(content, encoding="utf-8")
    descriptor = store.freeze_output_artifact(
        workspace,
        managed.activation_id,
        "documents/attribution.md",
        expected_name="attribution.md",
    )
    store.publish_managed_result(
        workspace,
        managed.activation_id,
        request_digest=f"digest-{request_key}",
        result={"attribution": asdict(descriptor)},
    )
    return descriptor


class _RecordingIssuePublisher:
    def __init__(self) -> None:
        self.calls: list[tuple[Path, str, str]] = []

    def create(
        self,
        *,
        repository_path: Path,
        title: str,
        body: str,
    ) -> CreatedIssue:
        self.calls.append((repository_path, title, body))
        return CreatedIssue(number=42, url="https://github.test/issues/42")


def test_proposal_to_issue_publishes_the_selected_attribution_document_unchanged(
    initialize_git_project,
) -> None:
    feature_path = initialize_git_project()
    database = SQLiteDatabase.for_project(feature_path)
    initialize_schema(database)
    store = AgentWorkspaceStore(
        project_path=feature_path,
        response_limit=65_536,
        artifact_limit=262_144,
    )
    first_document = "# First proposal\n\nKeep this content exactly.\n"
    first_descriptor = _publish_report(
        store,
        request_key="publish-first-report",
        content=first_document,
    )
    second_descriptor = _publish_report(
        store,
        request_key="publish-second-report",
        content="# Second proposal\n",
    )
    repository = SQLiteAutoTakeoverRepository()
    with database.transaction() as connection:
        first = repository.begin(
            connection,
            triage_id="feature-attribution",
            trigger_event_id=10,
        )
        assert first is not None
        repository.complete(
            connection,
            first[0].run_id,
            TakeoverStatus.NO,
            attribution=first_descriptor,
        )
        second = repository.begin(
            connection,
            triage_id="feature-attribution",
            trigger_event_id=20,
        )
        assert second is not None
        repository.complete(
            connection,
            second[0].run_id,
            TakeoverStatus.NO,
            attribution=second_descriptor,
        )

    publisher = _RecordingIssuePublisher()
    issue = ProposalToIssue(
        publisher=publisher,
        artifact_response_limit=65_536,
        artifact_limit=262_144,
    ).create(
        project=ManagedProject(
            project_id="project-1",
            name="Project",
            repository_path=feature_path,
            git_common_dir=feature_path / ".git",
            main_branch="main",
        ),
        feature=FeatureBinding(
            triage_id="feature-attribution",
            project_id="project-1",
            name="Repair delivery",
            worktree_path=feature_path,
        ),
        run_id=first[0].run_id,
    )

    assert issue == CreatedIssue(number=42, url="https://github.test/issues/42")
    assert publisher.calls == [
        (
            feature_path,
            "[AgentPanelX] Repair delivery",
            first_document,
        )
    ]


def test_workspace_exposes_recent_reports_and_current_running_state(
    initialize_git_project,
) -> None:
    project_path = initialize_git_project()
    database = SQLiteDatabase.for_project(project_path)
    initialize_schema(database)
    triage_id = "feature-attribution"
    with database.transaction() as connection:
        SQLiteProjectRuntimeStateRepository().insert(
            connection,
            ProjectRuntimeState(triage_id=triage_id, status="BLOCKED"),
        )

    store = AgentWorkspaceStore(
        project_path=project_path,
        response_limit=65_536,
        artifact_limit=262_144,
    )
    repository = SQLiteAutoTakeoverRepository()
    first_descriptor = _publish_report(
        store,
        request_key="first-report",
        content="# First proposal\n\n```mermaid\nflowchart LR\n  A --> B\n```\n",
    )
    second_descriptor = _publish_report(
        store,
        request_key="second-report",
        content="# Second proposal\n",
    )
    with database.transaction() as connection:
        first = repository.begin(
            connection,
            triage_id=triage_id,
            trigger_event_id=10,
        )
        assert first is not None
        repository.complete(
            connection,
            first[0].run_id,
            TakeoverStatus.NO,
            attribution=first_descriptor,
        )
        second = repository.begin(
            connection,
            triage_id=triage_id,
            trigger_event_id=20,
        )
        assert second is not None
        repository.complete(
            connection,
            second[0].run_id,
            TakeoverStatus.NO,
            attribution=second_descriptor,
        )
        running = repository.begin(
            connection,
            triage_id=triage_id,
            trigger_event_id=30,
        )
        assert running is not None

    view = ProjectWorkspaceQuery(
        database=database,
        git=GitRepository(project_path),
        artifacts=store,
        attribution_history_limit=1,
    ).get(triage_id)

    assert view.attribution_error is None
    assert view.attribution.state == "running"
    assert [report.run_id for report in view.attribution.reports] == [second[0].run_id]
    assert view.attribution.reports[0].trigger_event_id == 20
    assert view.attribution.reports[0].content_markdown == "# Second proposal\n"

    response = workspace_response(
        FeatureWorkspaceView(
            project=ManagedProject(
                project_id="project-1",
                name="Project",
                repository_path=project_path,
                git_common_dir=project_path / ".git",
                main_branch="main",
            ),
            binding=FeatureBinding(
                triage_id=triage_id,
                project_id="project-1",
                name="Attribution",
                worktree_path=project_path,
            ),
            runtime_view=view,
        )
    ).model_dump(mode="json")
    assert response["attribution"]["data"]["state"] == "running"
    assert len(response["attribution"]["data"]["reports"]) == 1
    assert response["attribution"]["data"]["reports"][0]["trigger_event_id"] == 20


def test_latest_failed_run_does_not_hide_older_report(
    initialize_git_project,
) -> None:
    project_path = initialize_git_project()
    database = SQLiteDatabase.for_project(project_path)
    initialize_schema(database)
    triage_id = "feature-attribution"
    with database.transaction() as connection:
        SQLiteProjectRuntimeStateRepository().insert(
            connection,
            ProjectRuntimeState(triage_id=triage_id, status="BLOCKED"),
        )

    store = AgentWorkspaceStore(project_path, 65_536, 262_144)
    descriptor = _publish_report(
        store,
        request_key="older-report",
        content="# Older proposal\n",
    )
    repository = SQLiteAutoTakeoverRepository()
    with database.transaction() as connection:
        completed = repository.begin(
            connection,
            triage_id=triage_id,
            trigger_event_id=10,
        )
        assert completed is not None
        repository.complete(
            connection,
            completed[0].run_id,
            TakeoverStatus.NO,
            attribution=descriptor,
        )
        failed = repository.begin(
            connection,
            triage_id=triage_id,
            trigger_event_id=20,
        )
        assert failed is not None
        repository.complete(
            connection,
            failed[0].run_id,
            TakeoverStatus.FAILED,
            error="attribution failed",
        )

    view = ProjectWorkspaceQuery(
        database=database,
        git=GitRepository(project_path),
        artifacts=store,
    ).get(triage_id)

    assert view.attribution.state == "failed"
    assert [report.run_id for report in view.attribution.reports] == [
        completed[0].run_id
    ]


def test_unreadable_report_is_local_to_that_history_item(
    initialize_git_project,
) -> None:
    project_path = initialize_git_project()
    database = SQLiteDatabase.for_project(project_path)
    initialize_schema(database)
    triage_id = "feature-attribution"
    with database.transaction() as connection:
        SQLiteProjectRuntimeStateRepository().insert(
            connection,
            ProjectRuntimeState(triage_id=triage_id, status="BLOCKED"),
        )

    store = AgentWorkspaceStore(project_path, 65_536, 262_144)
    descriptor = _publish_report(
        store,
        request_key="missing-report",
        content="# Will become unavailable\n",
    )
    Path(project_path / descriptor.project_relative_path).unlink()
    repository = SQLiteAutoTakeoverRepository()
    with database.transaction() as connection:
        started = repository.begin(
            connection,
            triage_id=triage_id,
            trigger_event_id=10,
        )
        assert started is not None
        repository.complete(
            connection,
            started[0].run_id,
            TakeoverStatus.NO,
            attribution=descriptor,
        )

    view = ProjectWorkspaceQuery(
        database=database,
        git=GitRepository(project_path),
        artifacts=store,
    ).get(triage_id)

    assert view.attribution_error is None
    assert view.attribution.state == "completed"
    assert len(view.attribution.reports) == 1
    assert view.attribution.reports[0].status == "unavailable"
    assert view.attribution.reports[0].content_markdown is None
