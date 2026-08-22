"""Public-boundary checks for the cohesive Delivery capability."""

from dataclasses import fields

from agentplanex.services.delivery.contracts import (
    CandidateDecision,
    MilestoneRunQueued,
)
from agentplanex.services.project_runtime import ProjectRuntimeService


def test_runtime_service_does_not_hold_delivery_mechanics() -> None:
    dependencies = {field.name for field in fields(ProjectRuntimeService)}
    assert dependencies.isdisjoint({"delivery_runner", "event_bus", "stage_runs"})


def test_run_receipt_does_not_expose_stage_run_entity() -> None:
    receipt_fields = {field.name for field in fields(MilestoneRunQueued)}
    assert "stage_run" not in receipt_fields
    assert {
        "run_id",
        "stage_run_id",
        "snapshot_id",
        "milestone_key",
        "stage_key",
        "input_commit_sha",
    }.issubset(receipt_fields)


def test_candidate_decision_receipt_does_not_expose_snapshot_entity() -> None:
    receipt_fields = {field.name for field in fields(CandidateDecision)}
    assert "snapshot" not in receipt_fields
    assert {
        "state",
        "identity",
        "decision",
        "result_snapshot_id",
        "next_milestone_key",
        "completed",
    } == receipt_fields
