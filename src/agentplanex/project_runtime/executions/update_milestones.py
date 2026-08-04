"""Project Runtime execution for publishing a complete Milestone View."""

from agentplanex.domains import (
    Milestone,
    MilestoneState,
    ProjectRuntimeContext,
    Stage,
    ToolArguments,
    ToolExecutionResult,
)
from agentplanex.project_owner_agent.tools import UPDATE_MILESTONES_TOOL
from agentplanex.project_runtime.executions.base import (
    ProjectExecution,
    project_execution,
)
from agentplanex.services.delivery import DeliveryError


@project_execution(UPDATE_MILESTONES_TOOL)
class UpdateMilestonesExecution(ProjectExecution):
    """Validate a Tool Action and publish its complete Milestone View."""

    def execute(
        self,
        context: ProjectRuntimeContext,
        arguments: ToolArguments,
    ) -> ToolExecutionResult:
        try:
            reason, milestones = self._arguments(arguments)
            updated = self.dependencies.delivery.update_milestones(
                context,
                reason=reason,
                milestones=milestones,
            )
        except DeliveryError as error:
            return ToolExecutionResult(output={"ok": False, "error": str(error)})
        except ValueError as error:
            return ToolExecutionResult(
                output={"ok": False, "error": f"Invalid Milestone View: {error}"}
            )

        review = updated.review
        output: dict[str, object] = {
            "ok": True,
            "accepted": updated.accepted,
            "triage_id": updated.context.triage_id,
            "status": updated.context.status,
            "review": {
                "decision": review.decision,
                "summary": review.summary,
                "required_changes": list(review.required_changes),
                "artifact": {
                    "uri": review.audit_artifact.uri,
                    "media_type": review.audit_artifact.media_type,
                    "size": review.audit_artifact.size,
                    "sha256": review.audit_artifact.sha256,
                },
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

    @staticmethod
    def _arguments(arguments: ToolArguments) -> tuple[str, tuple[Milestone, ...]]:
        if set(arguments) != {"reason", "milestones"}:
            raise DeliveryError(
                "update_milestones requires only reason and milestones arguments"
            )
        reason = arguments.get("reason")
        raw_milestones = arguments.get("milestones")
        if not isinstance(reason, str):
            raise DeliveryError("update_milestones reason must be a string")
        if not isinstance(raw_milestones, list):
            raise DeliveryError("update_milestones milestones must be an array")
        milestones: list[Milestone] = []
        for raw_milestone in raw_milestones:
            if not isinstance(raw_milestone, dict):
                raise DeliveryError("each Milestone must be an object")
            if set(raw_milestone) != {"key", "objective", "state", "stages"}:
                raise DeliveryError("each Milestone has unsupported fields")
            key = raw_milestone.get("key")
            objective = raw_milestone.get("objective")
            state = raw_milestone.get("state")
            raw_stages = raw_milestone.get("stages")
            if not isinstance(key, str) or not isinstance(objective, str):
                raise DeliveryError("Milestone key and objective must be strings")
            if not isinstance(state, str):
                raise DeliveryError("Milestone state must be a string")
            if not isinstance(raw_stages, list):
                raise DeliveryError("Milestone stages must be an array")
            stages: list[Stage] = []
            for raw_stage in raw_stages:
                if not isinstance(raw_stage, dict) or set(raw_stage) != {"key", "objective"}:
                    raise DeliveryError("each Stage must contain only key and objective")
                stage_key = raw_stage.get("key")
                stage_objective = raw_stage.get("objective")
                if not isinstance(stage_key, str) or not isinstance(stage_objective, str):
                    raise DeliveryError("Stage key and objective must be strings")
                stages.append(Stage(key=stage_key, objective=stage_objective))
            try:
                milestone_state = MilestoneState(state)
            except ValueError as error:
                raise DeliveryError("Milestone state must be pending or completed") from error
            milestones.append(
                Milestone(
                    key=key,
                    objective=objective,
                    state=milestone_state,
                    stages=tuple(stages),
                )
            )
        return reason, tuple(milestones)
