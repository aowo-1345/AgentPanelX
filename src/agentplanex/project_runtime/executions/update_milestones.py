"""Project Runtime execution for publishing a complete Milestone View."""

from typing import Literal, Self

from pydantic import Field, model_validator

from agentplanex.domains.project_runtime_state import ProjectRuntimeState
from agentplanex.project_owner_agent.contracts import ToolExecutionResult
from agentplanex.project_owner_agent.tools import (
    NonBlankText,
    ToolArgumentsModel,
    ToolDefinition,
    ToolIdentifier,
)
from agentplanex.project_runtime.executions.base import (
    ProjectExecution,
    project_execution,
)
from agentplanex.services.delivery.contracts import DeliveryError
from agentplanex.services.delivery.models import Milestone, MilestoneState, Stage

UPDATE_MILESTONES_TOOL_NAME = "update_milestones"
UPDATE_MILESTONES_DESCRIPTION = (
    "Replace the complete Milestone View derived from the approved canonical Plan. "
    "This is a full replacement, not a patch. Use it for the initial delivery "
    "breakdown or when remaining objectives/order must change; Candidate acceptance "
    "alone records completion. Runtime invokes the Milestone Hard Gate only while "
    "rolling delivery is IN_PROGRESS."
)


class StageArguments(ToolArgumentsModel):
    key: ToolIdentifier = Field(description="Stable Stage identifier.")
    objective: NonBlankText = Field(description="Observable Stage outcome.")

    def to_domain(self) -> Stage:
        return Stage(key=self.key, objective=self.objective)


class MilestoneArguments(ToolArgumentsModel):
    key: ToolIdentifier = Field(description="Stable Milestone identifier.")
    objective: NonBlankText = Field(description="Observable Milestone outcome.")
    state: Literal["pending", "completed"] = Field(
        description="Current delivery state represented by the complete View."
    )
    stages: list[StageArguments] = Field(
        min_length=1,
        description="Ordered Stages needed to deliver this Milestone.",
    )

    def to_domain(self) -> Milestone:
        return Milestone(
            key=self.key,
            objective=self.objective,
            state=MilestoneState(self.state),
            stages=tuple(stage.to_domain() for stage in self.stages),
        )


class UpdateMilestonesArguments(ToolArgumentsModel):
    reason: NonBlankText = Field(
        description="Why the complete Milestone View is being replaced."
    )
    milestones: list[MilestoneArguments] = Field(
        min_length=1,
        description=(
            "The complete ordered Milestone View, including completed history and at "
            "least one pending Milestone."
        ),
    )

    @model_validator(mode="after")
    def require_pending_milestone(self) -> Self:
        if not any(milestone.state == "pending" for milestone in self.milestones):
            raise ValueError("Milestone View must contain a pending Milestone")
        return self

    def domain_milestones(self) -> tuple[Milestone, ...]:
        return tuple(milestone.to_domain() for milestone in self.milestones)


UPDATE_MILESTONES_TOOL = ToolDefinition(
    name=UPDATE_MILESTONES_TOOL_NAME,
    description=UPDATE_MILESTONES_DESCRIPTION,
    arguments_type=UpdateMilestonesArguments,
)


@project_execution(UPDATE_MILESTONES_TOOL)
class UpdateMilestonesExecution(ProjectExecution[UpdateMilestonesArguments]):
    """Validate a Tool Action and publish its complete Milestone View."""

    def execute(
        self,
        _context: ProjectRuntimeState,
        arguments: UpdateMilestonesArguments,
    ) -> ToolExecutionResult:
        try:
            updated = self.dependencies.delivery.update_milestones(
                reason=arguments.reason,
                milestones=arguments.domain_milestones(),
            )
        except DeliveryError as error:
            return ToolExecutionResult(output={"ok": False, "error": str(error)})
        except ValueError as error:
            return ToolExecutionResult(
                output={"ok": False, "error": f"Invalid Milestone View: {error}"}
            )

        output: dict[str, object] = {
            "ok": True,
            "accepted": updated.accepted,
            "triage_id": updated.state.triage_id,
            "status": updated.state.status,
            "subject_digest": updated.subject_digest,
            "hard_gate_invoked": updated.review is not None,
            "review": None,
        }
        review = updated.review
        if review is not None:
            output["review"] = {
                "decision": review.decision,
                "summary": review.summary,
                "required_changes": list(review.required_changes),
                "artifact": {
                    "uri": review.audit_artifact.uri,
                    "project_relative_path": (
                        review.audit_artifact.project_relative_path
                    ),
                    "media_type": review.audit_artifact.media_type,
                    "size": review.audit_artifact.size,
                    "sha256": review.audit_artifact.sha256,
                },
            }
        if updated.snapshot is not None:
            output["snapshot"] = {
                "snapshot_id": updated.snapshot.snapshot_id,
                "previous_snapshot_id": updated.snapshot.previous_snapshot_id,
                "plan_commit_sha": updated.snapshot.plan_commit_sha,
                "milestone_count": len(updated.snapshot.milestones),
            }
        return ToolExecutionResult(output=output)
