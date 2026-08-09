"""Typed HTTP contracts and mapping from existing application read models."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agentplanex.domains import BoardFeature, FeatureView, ManagedProject, OwnerActivation
from agentplanex.services.workspace import FeatureWorkspace


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


class MessageRequest(Schema):
    content: str = Field(min_length=1)


class ActivationResponse(Schema):
    activation_id: str
    status: str
    created_at: datetime


type HumanAction = Literal[
    "begin", "approve-plan", "reject-plan", "start-delivery"
]


class ActionRequest(Schema):
    action: HumanAction
    feedback: str | None = None

    @model_validator(mode="after")
    def require_rejection_feedback(self) -> "ActionRequest":
        if self.action == "reject-plan" and not (self.feedback or "").strip():
            raise ValueError("reject-plan requires non-empty feedback")
        return self


class RuntimeData(Schema):
    status: str
    pending_action: str | None
    activation_status: str | None
    current_milestone_key: str | None
    current_stage_key: str | None


class ConversationMessage(Schema):
    message_id: str
    role: Literal["user", "assistant", "status"]
    content: str


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


class WorkspaceResponse(Schema):
    project: ProjectResponse
    feature: BoardFeatureResponse
    available_actions: list[HumanAction]
    runtime: Panel[RuntimeData]
    conversation: Panel[list[ConversationMessage]]
    plan: Panel[PlanData]
    milestones: Panel[MilestonesData]
    timeline: Panel[list[TimelineEventData]]
    git: Panel[GitData]


def project_response(project: ManagedProject) -> ProjectResponse:
    return ProjectResponse(
        project_id=project.project_id,
        name=project.name,
        repository_path=str(project.repository_path),
        main_branch=project.main_branch,
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


def workspace_response(workspace: FeatureWorkspace) -> WorkspaceResponse:
    control = workspace.control
    context = control.context
    feature = BoardFeatureResponse(
        project_id=workspace.project.project_id,
        project_name=workspace.project.name,
        triage_id=workspace.binding.triage_id,
        name=workspace.binding.name,
        status=context.status,
        branch=control.git_branch or context.git_branch,
        pending_action=context.pending_action,
        current_milestone_key=context.current_milestone_key,
        current_stage_key=context.current_stage_key,
    )
    snapshot = control.snapshot
    human_actions: list[HumanAction] = []
    if context.status == "TRIAGE" and control.owner_activation is None:
        human_actions.append("begin")
    if context.pending_action == "PLAN_APPROVAL":
        human_actions.extend(("approve-plan", "reject-plan"))
    elif context.pending_action == "FIRST_RUN_APPROVAL":
        human_actions.append("start-delivery")
    return WorkspaceResponse(
        project=project_response(workspace.project),
        feature=feature,
        available_actions=human_actions,
        runtime=Panel(
            data=RuntimeData(
                status=context.status,
                pending_action=context.pending_action,
                activation_status=(
                    control.owner_activation.status.value
                    if control.owner_activation is not None
                    else None
                ),
                current_milestone_key=context.current_milestone_key,
                current_stage_key=context.current_stage_key,
            )
        ),
        conversation=Panel(
            data=[
                ConversationMessage(
                    message_id=message.message_id,
                    role=message.role,
                    content=message.content,
                )
                for message in control.conversation
            ]
        ),
        plan=Panel(
            data=(
                PlanData(
                    documents=[
                        PlanDocumentData(name=item.name, content=item.content)
                        for item in control.plan_documents
                    ],
                    pending_subject_digest=context.pending_plan_subject_digest,
                    current_commit_sha=context.current_plan_commit_sha,
                )
                if control.plan_error is None
                else None
            ),
            error=control.plan_error,
        ),
        milestones=Panel(
            data=MilestonesData(
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
        ),
        timeline=Panel(
            data=[
                TimelineEventData(
                    event_id=event.event_id,
                    event_type=event.event_type.value,
                    created_at=event.created_at,
                    payload=event.payload,
                )
                for event in control.timeline
            ]
        ),
        git=Panel(
            data=(
                GitData(branch=control.git_branch, head=control.git_head)
                if control.git_branch is not None and control.git_head is not None
                else None
            ),
            error=control.git_error,
        ),
    )
