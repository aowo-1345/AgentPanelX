"""Publish one stored Attribution Proposal as one GitHub Issue."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from agentplanex.domains.workspace import FeatureBinding, ManagedProject
from agentplanex.infrastructure.agent_workspace import AgentWorkspaceError, AgentWorkspaceStore
from agentplanex.infrastructure.sqlite import SQLiteDatabase
from agentplanex.infrastructure.sqlite.repositories import SQLiteAutoTakeoverRepository


class GitHubIssueError(RuntimeError):
    """The GitHub Issue request could not be completed."""


@dataclass(frozen=True, slots=True)
class CreatedIssue:
    number: int
    url: str


class IssuePublisher(Protocol):
    def create(
        self,
        *,
        repository_path: Path,
        title: str,
        body: str,
    ) -> CreatedIssue: ...


@dataclass(slots=True)
class ProposalToIssue:
    """Select one Proposal and pass its document unchanged to the publisher."""

    publisher: IssuePublisher
    artifact_response_limit: int
    artifact_limit: int

    def create(
        self,
        *,
        project: ManagedProject,
        feature: FeatureBinding,
        run_id: str,
    ) -> CreatedIssue:
        selected_run_id = run_id.strip()
        if not selected_run_id:
            raise ValueError("Attribution Proposal run ID must not be empty")
        if feature.project_id != project.project_id:
            raise LookupError("Attribution Proposal not found")

        database = SQLiteDatabase.for_project(feature.worktree_path)
        with database.read_only_connection() as connection:
            run = SQLiteAutoTakeoverRepository().get(connection, selected_run_id)
        if (
            run is None
            or run.triage_id != feature.triage_id
            or run.attribution is None
        ):
            raise LookupError("Attribution Proposal not found")
        if run.issue_number is not None and run.issue_url is not None:
            return CreatedIssue(number=run.issue_number, url=run.issue_url)

        artifacts = AgentWorkspaceStore(
            project_path=feature.worktree_path,
            response_limit=self.artifact_response_limit,
            artifact_limit=self.artifact_limit,
        )
        try:
            body = artifacts.read_descriptor_text(run.attribution)
        except AgentWorkspaceError as error:
            raise ValueError("Attribution Proposal content is unavailable") from error

        issue = self.publisher.create(
            repository_path=project.repository_path,
            title=f"[AgentPanelX] {feature.name}",
            body=body,
        )
        with database.transaction() as connection:
            SQLiteAutoTakeoverRepository().record_created_issue(
                connection,
                selected_run_id,
                number=issue.number,
                url=issue.url,
            )
        return issue
