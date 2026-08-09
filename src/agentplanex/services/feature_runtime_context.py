"""Strictly read-only access to one bound Feature Runtime Context."""

import sqlite3
from dataclasses import dataclass, field

from agentplanex.domains import FeatureBinding, ProjectRuntimeContext
from agentplanex.infrastructure.sqlite import SQLiteDatabase
from agentplanex.infrastructure.sqlite.repositories import (
    SQLiteProjectRuntimeContextRepository,
)


@dataclass(frozen=True, slots=True)
class FeatureRuntimeContextQuery:
    """Load the exact Context named by a Registry binding without initializing it."""

    contexts: SQLiteProjectRuntimeContextRepository = field(
        default_factory=SQLiteProjectRuntimeContextRepository
    )

    def get(self, binding: FeatureBinding) -> ProjectRuntimeContext:
        database = SQLiteDatabase.for_project(binding.worktree_path)
        try:
            with database.read_only_connection() as connection:
                context = self.contexts.get(connection, binding.triage_id)
        except sqlite3.Error as error:
            raise LookupError(
                f"Feature Runtime database is unavailable: {binding.triage_id}"
            ) from error
        if context is None:
            raise LookupError(
                f"Feature Runtime Context not found: {binding.triage_id}"
            )
        return context
