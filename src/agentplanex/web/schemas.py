"""Typed HTTP contracts and mapping from existing application read models."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agentplanex.domains.workspace import (
    BoardFeature,
    FeatureAction,
    FeatureView,
    ManagedProject,
)
from agentplanex.services.project_runtime_context.models import OwnerActivation
from agentplanex.services.workspace.queries import FeatureWorkspaceView


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Panel[T](Schema):
    data: T | None
    error: str | None = None


class CreateProjectRequest(Schema):
    name: str = Field(min_length=1)
    repository_path: str = Field(min_length=1)
    main_branch: str = Field(min_length=1)


class ProjectResponse(Schema):
    project_id: str
    name: str
    repository_path: str
    main_branch: str
    git_version: str | None = None


class CreateFeatureRequest(Schema):
    name: str = Field(min_length=1)


class FeatureResponse(Schema):
    triage_id: str
    project_id: str
    name: str
    branch: str
    worktree_path: str


class BoardFeatureResponse(Schema):
    project_id: str
    project_name: str
    triage_id: str
    name: str
    status: str
    branch: str | None
    pending_action: str | None
    current_milestone_key: str | None
    current_stage_key: str | None


class WorkspaceFeatureResponse(BoardFeatureResponse):
    worktree_path: str


class MessageRequest(Schema):
    content: str = Field(min_length=1)


class ActivationResponse(Schema):
    activation_id: str
    status: str
    created_at: datetime


class ActionRequest(Schema):
    action: FeatureAction
    feedback: str | None = None

    @model_validator(mode="after")
    def require_rejection_feedback(self) -> "ActionRequest":
        if (
            self.action
            in {
                FeatureAction.REJECT_PLAN,
                FeatureAction.REJECT_BLOCKED_RUN,
            }
            and not (self.feedback or "").strip()
        ):
            raise ValueError(f"{self.action} requires non-empty feedback")
        return self


class RuntimeData(Schema):
    status: str
    pending_action: str | None
    activation_status: str | None
    activation_has_reply: bool
    current_milestone_key: str | None
    current_stage_key: str | None
    blocked_reason: str | None
    blocked_capability: str | None


class ToolActivityData(Schema):
    name: str
    status: Literal["running", "completed", "failed"]
    input_preview: str
    output_preview: str | None


class ConversationMessage(Schema):
    message_id: str
    role: Literal["user", "assistant", "status", "tool"]
    content: str
    tool_activity: ToolActivityData | None


class PlanDocumentData(Schema):
    name: str
    content: str | None


class PlanData(Schema):
    documents: list[PlanDocumentData]
    pending_subject_digest: str | None
    current_commit_sha: str | None


class StageData(Schema):
    key: str
    objective: str


class MilestoneData(Schema):
    key: str
    objective: str
    state: str
    stages: list[StageData]


class MilestonesData(Schema):
    snapshot_id: str | None
    milestones: list[MilestoneData]


class TimelineEventData(Schema):
    event_id: int | None
    event_type: str
    created_at: datetime
    payload: dict[str, object]


class GitData(Schema):
    branch: str
    head: str


class AttributionReportData(Schema):
    run_id: str
    created_at: datetime
    completed_at: datetime | None
    status: Literal["available", "unavailable"]
    content_markdown: str | None


class AttributionPanelData(Schema):
    state: Literal["idle", "running", "completed", "failed"]
    reports: list[AttributionReportData]


class WorkspaceResponse(Schema):
    project: ProjectResponse
    feature: WorkspaceFeatureResponse
    available_actions: list[FeatureAction]
    runtime: Panel[RuntimeData]
    conversation: Panel[list[ConversationMessage]]
    plan: Panel[PlanData]
    milestones: Panel[MilestonesData]
    timeline: Panel[list[TimelineEventData]]
    git: Panel[GitData]
    attribution: Panel[AttributionPanelData]


def project_response(
    project: ManagedProject,
    *,
    git_version: str | None = None,
) -> ProjectResponse:
    return ProjectResponse(
        project_id=project.project_id,
        name=project.name,
        repository_path=str(project.repository_path),
        main_branch=project.main_branch,
        git_version=git_version,
    )


def feature_response(feature: FeatureView) -> FeatureResponse:
    return FeatureResponse(
        triage_id=feature.triage_id,
        project_id=feature.project_id,
        name=feature.name,
        branch=feature.branch,
        worktree_path=str(feature.worktree_path),
    )


def board_feature_response(
    feature: BoardFeature,
    project_name: str,
) -> BoardFeatureResponse:
    return BoardFeatureResponse(
        project_id=feature.project_id,
        project_name=project_name,
        triage_id=feature.triage_id,
        name=feature.name,
        status=feature.status,
        branch=feature.branch,
        pending_action=feature.pending_action,
        current_milestone_key=feature.current_milestone_key,
        current_stage_key=feature.current_stage_key,
    )


def activation_response(activation: OwnerActivation) -> ActivationResponse:
    return ActivationResponse(
        activation_id=activation.activation_id,
        status=activation.status.value,
        created_at=activation.created_at,
    )


def workspace_response(workspace: FeatureWorkspaceView) -> WorkspaceResponse:
    runtime_view = workspace.runtime_view
    context = runtime_view.state
    feature = WorkspaceFeatureResponse(
        project_id=workspace.project.project_id,
        project_name=workspace.project.name,
        triage_id=workspace.binding.triage_id,
        name=workspace.binding.name,
        status=context.status,
        branch=runtime_view.git_branch or context.git_branch,
        pending_action=context.pending_action,
        current_milestone_key=context.current_milestone_key,
        current_stage_key=context.current_stage_key,
        worktree_path=str(workspace.binding.worktree_path),
    )
    snapshot = runtime_view.snapshot
    return WorkspaceResponse(
        project=project_response(workspace.project),
        feature=feature,
        available_actions=list(runtime_view.available_actions),
        runtime=Panel(
            data=(
                RuntimeData(
                    status=context.status,
                    pending_action=context.pending_action,
                    activation_status=(
                        runtime_view.owner_activation.status.value
                        if runtime_view.owner_activation is not None
                        else None
                    ),
                    activation_has_reply=runtime_view.activation_has_reply,
                    current_milestone_key=context.current_milestone_key,
                    current_stage_key=context.current_stage_key,
                    blocked_reason=context.blocked_reason,
                    blocked_capability=context.blocked_capability,
                )
                if runtime_view.runtime_error is None
                else None
            ),
            error=runtime_view.runtime_error,
        ),
        conversation=Panel(
            data=(
                [
                    ConversationMessage(
                        message_id=message.message_id,
                        role=message.role,
                        content=message.content,
                        tool_activity=(
                            ToolActivityData(
                                name=message.tool_activity.name,
                                status=message.tool_activity.status,
                                input_preview=message.tool_activity.input_preview,
                                output_preview=message.tool_activity.output_preview,
                            )
                            if message.tool_activity is not None
                            else None
                        ),
                    )
                    for message in runtime_view.conversation
                ]
                if runtime_view.conversation_error is None
                else None
            ),
            error=runtime_view.conversation_error,
        ),
        plan=Panel(
            data=(
                PlanData(
                    documents=[
                        PlanDocumentData(name=item.name, content=item.content)
                        for item in runtime_view.plan_documents
                    ],
                    pending_subject_digest=context.pending_plan_subject_digest,
                    current_commit_sha=context.current_plan_commit_sha,
                )
                if runtime_view.plan_error is None
                else None
            ),
            error=runtime_view.plan_error,
        ),
        milestones=Panel(
            data=(
                MilestonesData(
                    snapshot_id=snapshot.snapshot_id if snapshot is not None else None,
                    milestones=(
                        [
                            MilestoneData(
                                key=milestone.key,
                                objective=milestone.objective,
                                state=milestone.state.value,
                                stages=[
                                    StageData(key=stage.key, objective=stage.objective)
                                    for stage in milestone.stages
                                ],
                            )
                            for milestone in snapshot.milestones
                        ]
                        if snapshot is not None
                        else []
                    ),
                )
                if runtime_view.milestones_error is None
                else None
            ),
            error=runtime_view.milestones_error,
        ),
        timeline=Panel(
            data=(
                [
                    TimelineEventData(
                        event_id=event.event_id,
                        event_type=event.event_type.value,
                        created_at=event.created_at,
                        payload=event.payload,
                    )
                    for event in runtime_view.timeline
                ]
                if runtime_view.timeline_error is None
                else None
            ),
            error=runtime_view.timeline_error,
        ),
        attribution=Panel(
            data=(
                AttributionPanelData(
                    state=runtime_view.attribution.state,
                    reports=[
                        AttributionReportData(
                            run_id=report.run_id,
                            created_at=report.created_at,
                            completed_at=report.completed_at,
                            status=report.status,
                            content_markdown=report.content_markdown,
                        )
                        for report in runtime_view.attribution.reports
                    ],
                )
                if runtime_view.attribution_error is None
                else None
            ),
            error=runtime_view.attribution_error,
        ),
        git=Panel(
            data=(
                GitData(branch=runtime_view.git_branch, head=runtime_view.git_head)
                if runtime_view.git_branch is not None and runtime_view.git_head is not None
                else None
            ),
            error=runtime_view.git_error,
        ),
    )
