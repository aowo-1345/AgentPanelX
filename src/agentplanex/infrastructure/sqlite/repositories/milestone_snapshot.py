"""SQLite persistence for immutable Milestone Snapshots."""

import json
import sqlite3
from datetime import datetime
from typing import cast

from agentplanex.services.delivery.models import (
    Milestone,
    MilestoneSnapshot,
    MilestoneState,
    Stage,
    milestone_view_json,
)


class SQLiteMilestoneSnapshotRepository:
    """Persist complete Milestone Views without normalizing their definitions."""

    def insert(
        self,
        connection: sqlite3.Connection,
        snapshot: MilestoneSnapshot,
    ) -> None:
        connection.execute(
            """
            INSERT INTO milestone_snapshot (
                snapshot_id,
                triage_id,
                previous_snapshot_id,
                plan_commit_sha,
                milestones,
                reason,
                message_id,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot.snapshot_id,
                snapshot.triage_id,
                snapshot.previous_snapshot_id,
                snapshot.plan_commit_sha,
                milestone_view_json(snapshot.milestones),
                snapshot.reason,
                snapshot.message_id,
                snapshot.created_at.isoformat(),
            ),
        )

    def get(
        self,
        connection: sqlite3.Connection,
        snapshot_id: str,
    ) -> MilestoneSnapshot | None:
        row = connection.execute(
            f"{self._SELECT} WHERE snapshot_id = ?",
            (snapshot_id,),
        ).fetchone()
        return self._from_row(row) if row is not None else None

    def list_by_triage_id(
        self,
        connection: sqlite3.Connection,
        triage_id: str,
    ) -> tuple[MilestoneSnapshot, ...]:
        rows = connection.execute(
            f"{self._SELECT} WHERE triage_id = ? ORDER BY created_at, snapshot_id",
            (triage_id,),
        ).fetchall()
        return tuple(self._from_row(row) for row in rows)

    _SELECT = """
        SELECT
            snapshot_id,
            triage_id,
            previous_snapshot_id,
            plan_commit_sha,
            milestones,
            reason,
            message_id,
            created_at
        FROM milestone_snapshot
    """

    @staticmethod
    def _from_row(row: sqlite3.Row) -> MilestoneSnapshot:
        return MilestoneSnapshot(
            snapshot_id=cast(str, row["snapshot_id"]),
            triage_id=cast(str, row["triage_id"]),
            previous_snapshot_id=cast(str | None, row["previous_snapshot_id"]),
            plan_commit_sha=cast(str, row["plan_commit_sha"]),
            milestones=_decode_milestones(cast(str, row["milestones"])),
            reason=cast(str, row["reason"]),
            message_id=cast(str | None, row["message_id"]),
            created_at=datetime.fromisoformat(cast(str, row["created_at"])),
        )


def _decode_milestones(raw: str) -> tuple[Milestone, ...]:
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError("Stored Milestone Snapshot contains invalid JSON") from error
    if not isinstance(decoded, list):
        raise ValueError("Stored Milestone Snapshot must contain an array")
    milestones: list[Milestone] = []
    for item in decoded:
        if not isinstance(item, dict):
            raise ValueError("Stored Milestone must be an object")
        key = item.get("key")
        objective = item.get("objective")
        state = item.get("state")
        stages = item.get("stages")
        if not isinstance(key, str) or not isinstance(objective, str):
            raise ValueError("Stored Milestone has invalid key or objective")
        if not isinstance(state, str):
            raise ValueError("Stored Milestone has invalid state")
        if not isinstance(stages, list):
            raise ValueError("Stored Milestone stages must be an array")
        decoded_stages: list[Stage] = []
        for stage in stages:
            if not isinstance(stage, dict):
                raise ValueError("Stored Stage must be an object")
            stage_key = stage.get("key")
            stage_objective = stage.get("objective")
            if not isinstance(stage_key, str) or not isinstance(stage_objective, str):
                raise ValueError("Stored Stage has invalid key or objective")
            decoded_stages.append(Stage(key=stage_key, objective=stage_objective))
        try:
            milestone_state = MilestoneState(state)
        except ValueError as error:
            raise ValueError("Stored Milestone has unsupported state") from error
        milestones.append(
            Milestone(
                key=key,
                objective=objective,
                state=milestone_state,
                stages=tuple(decoded_stages),
            )
        )
    return tuple(milestones)
