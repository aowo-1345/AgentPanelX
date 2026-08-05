"""SQLite connection and transaction management."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

PROJECT_DATA_DIRECTORY = ".agentplanex"
DATABASE_FILENAME = "agentplanex.sqlite3"


@dataclass(frozen=True, slots=True)
class SQLiteDatabase:
    """Open configured SQLite connections for one database file."""

    path: Path
    busy_timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        if self.busy_timeout_seconds <= 0:
            raise ValueError("busy_timeout_seconds must be positive")

    @classmethod
    def for_project(
        cls,
        project_path: Path,
        *,
        busy_timeout_seconds: float = 5.0,
    ) -> "SQLiteDatabase":
        """Use the standard database location inside one developed project."""
        return cls(
            path=project_path / PROJECT_DATA_DIRECTORY / DATABASE_FILENAME,
            busy_timeout_seconds=busy_timeout_seconds,
        )

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        """Open an autocommit connection and close it after use."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            self.path,
            timeout=self.busy_timeout_seconds,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute(
            f"PRAGMA busy_timeout = {int(self.busy_timeout_seconds * 1000)}"
        )
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def read_only_connection(self) -> Iterator[sqlite3.Connection]:
        """Open an existing database with SQLite-enforced read-only access."""

        connection = sqlite3.connect(
            f"{self.path.resolve().as_uri()}?mode=ro",
            uri=True,
            timeout=self.busy_timeout_seconds,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        connection.execute(
            f"PRAGMA busy_timeout = {int(self.busy_timeout_seconds * 1000)}"
        )
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Run writes atomically on one immediate transaction."""
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except BaseException:
                connection.rollback()
                raise
            else:
                connection.commit()
