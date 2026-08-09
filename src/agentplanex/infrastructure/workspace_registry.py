"""SQLite Registry for user-managed Projects and Feature Runtime locations."""

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from agentplanex.domains import FeatureBinding, ManagedProject
from agentplanex.infrastructure.sqlite import SQLiteDatabase

REGISTRY_SCHEMA_VERSION = 1

_SCHEMA = (
    """
    CREATE TABLE managed_project (
        project_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        repository_path TEXT NOT NULL,
        git_common_dir TEXT NOT NULL UNIQUE,
        main_branch TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE feature_binding (
        triage_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES managed_project(project_id),
        name TEXT NOT NULL,
        worktree_path TEXT NOT NULL UNIQUE
    )
    """,
)


@dataclass(frozen=True, slots=True)
class WorkspaceRegistry:
    """Persist only Project identity and Feature-to-Runtime bindings."""

    database: SQLiteDatabase

    @classmethod
    def at(cls, path: Path) -> "WorkspaceRegistry":
        return cls(SQLiteDatabase(path))

    def initialize(self) -> None:
        with self.database.transaction() as connection:
            row = connection.execute("PRAGMA user_version").fetchone()
            version = int(row[0]) if row is not None else 0
            if version == 0:
                for statement in _SCHEMA:
                    connection.execute(statement)
                connection.execute(f"PRAGMA user_version = {REGISTRY_SCHEMA_VERSION}")
            elif version != REGISTRY_SCHEMA_VERSION:
                raise RuntimeError(f"Unsupported Workspace Registry version: {version}")

    def insert_project(self, project: ManagedProject) -> None:
        try:
            with self.database.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO managed_project (
                        project_id, name, repository_path, git_common_dir, main_branch
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        project.project_id,
                        project.name,
                        str(project.repository_path),
                        str(project.git_common_dir),
                        project.main_branch,
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise ValueError("Git repository is already registered") from error

    def get_project(self, project_id: str) -> ManagedProject:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM managed_project WHERE project_id = ?",
                (project_id,),
            ).fetchone()
        if row is None:
            raise LookupError(f"Managed Project not found: {project_id}")
        return _project_from_row(row)

    def find_project_by_common_dir(self, common_dir: Path) -> ManagedProject | None:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM managed_project WHERE git_common_dir = ?",
                (str(common_dir),),
            ).fetchone()
        return _project_from_row(row) if row is not None else None

    def list_projects(self) -> tuple[ManagedProject, ...]:
        with self.database.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM managed_project ORDER BY name, project_id"
            ).fetchall()
        return tuple(_project_from_row(row) for row in rows)

    def insert_feature(self, feature: FeatureBinding) -> None:
        try:
            with self.database.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO feature_binding (
                        triage_id, project_id, name, worktree_path
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        feature.triage_id,
                        feature.project_id,
                        feature.name,
                        str(feature.worktree_path),
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise ValueError(
                "Feature binding conflicts with existing Registry data: "
                f"{feature.triage_id}"
            ) from error

    def get_feature(self, project_id: str, triage_id: str) -> FeatureBinding:
        with self.database.connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM feature_binding
                WHERE project_id = ? AND triage_id = ?
                """,
                (project_id, triage_id),
            ).fetchone()
        if row is None:
            raise LookupError(
                f"Feature not found in managed Project: {project_id}/{triage_id}"
            )
        return _feature_from_row(row)

    def list_features(self, project_id: str) -> tuple[FeatureBinding, ...]:
        self.get_project(project_id)
        with self.database.connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM feature_binding
                WHERE project_id = ?
                ORDER BY name, triage_id
                """,
                (project_id,),
            ).fetchall()
        return tuple(_feature_from_row(row) for row in rows)

    def delete_feature(self, project_id: str, triage_id: str) -> None:
        with self.database.transaction() as connection:
            cursor = connection.execute(
                """
                DELETE FROM feature_binding
                WHERE project_id = ? AND triage_id = ?
                """,
                (project_id, triage_id),
            )
            if cursor.rowcount != 1:
                raise LookupError(
                    f"Feature not found in managed Project: {project_id}/{triage_id}"
                )


def _project_from_row(row: sqlite3.Row) -> ManagedProject:
    return ManagedProject(
        project_id=str(row["project_id"]),
        name=str(row["name"]),
        repository_path=Path(row["repository_path"]),
        git_common_dir=Path(row["git_common_dir"]),
        main_branch=str(row["main_branch"]),
    )


def _feature_from_row(row: sqlite3.Row) -> FeatureBinding:
    return FeatureBinding(
        triage_id=str(row["triage_id"]),
        project_id=str(row["project_id"]),
        name=str(row["name"]),
        worktree_path=Path(row["worktree_path"]),
    )
