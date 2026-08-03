"""SQLite operations for Project Owner Agents."""

import json
import sqlite3
from typing import cast

from agentplanex.domains import ProjectOwnerAgent


class SQLiteProjectOwnerAgentRepository:
    """Insert, update, and query persisted Project Owner Agents."""

    def insert(
        self,
        connection: sqlite3.Connection,
        agent: ProjectOwnerAgent,
    ) -> None:
        connection.execute(
            """
            INSERT INTO project_owner_agent (
                triage_id,
                project_owner_session_id,
                system_prompt,
                tools,
                summary_id,
                message_id
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            self._values(agent),
        )

    def update(
        self,
        connection: sqlite3.Connection,
        agent: ProjectOwnerAgent,
    ) -> None:
        cursor = connection.execute(
            """
            UPDATE project_owner_agent
            SET triage_id = ?, system_prompt = ?, tools = ?, summary_id = ?, message_id = ?
            WHERE project_owner_session_id = ?
            """,
            (
                agent.triage_id,
                agent.system_prompt,
                self._encode_tools(agent.tools),
                agent.summary_id,
                agent.message_id,
                agent.project_owner_session_id,
            ),
        )
        if cursor.rowcount != 1:
            raise LookupError(
                f"Project Owner Agent not found: {agent.project_owner_session_id}"
            )

    def get_by_session_id(
        self,
        connection: sqlite3.Connection,
        session_id: str,
    ) -> ProjectOwnerAgent | None:
        row = connection.execute(
            f"{self._SELECT} WHERE project_owner_session_id = ?",
            (session_id,),
        ).fetchone()
        return self._from_row(row) if row is not None else None

    def get_by_triage_id(
        self,
        connection: sqlite3.Connection,
        triage_id: str,
    ) -> ProjectOwnerAgent | None:
        row = connection.execute(
            f"{self._SELECT} WHERE triage_id = ?",
            (triage_id,),
        ).fetchone()
        return self._from_row(row) if row is not None else None

    _SELECT = """
        SELECT
            triage_id,
            project_owner_session_id,
            system_prompt,
            tools,
            summary_id,
            message_id
        FROM project_owner_agent
    """

    @classmethod
    def _values(cls, agent: ProjectOwnerAgent) -> tuple[object, ...]:
        return (
            agent.triage_id,
            agent.project_owner_session_id,
            agent.system_prompt,
            cls._encode_tools(agent.tools),
            agent.summary_id,
            agent.message_id,
        )

    @staticmethod
    def _encode_tools(tools: tuple[str, ...]) -> str:
        return json.dumps(tools, ensure_ascii=True, separators=(",", ":"))

    @staticmethod
    def _decode_tools(value: str) -> tuple[str, ...]:
        decoded: object = json.loads(value)
        if not isinstance(decoded, list) or not all(
            isinstance(tool, str) for tool in decoded
        ):
            raise ValueError("Stored Project Owner Agent tools must be a JSON string array")
        return tuple(decoded)

    @classmethod
    def _from_row(cls, row: sqlite3.Row) -> ProjectOwnerAgent:
        return ProjectOwnerAgent(
            triage_id=cast(str, row["triage_id"]),
            project_owner_session_id=cast(str, row["project_owner_session_id"]),
            system_prompt=cast(str, row["system_prompt"]),
            tools=cls._decode_tools(cast(str, row["tools"])),
            summary_id=cast(str | None, row["summary_id"]),
            message_id=cast(str | None, row["message_id"]),
        )
