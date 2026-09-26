"""Read-only Code Graph projections shared by Workspace and Web."""

from dataclasses import dataclass
from typing import Literal

CodeGraphStatus = Literal[
    "disabled",
    "pending",
    "waiting_clean",
    "analyzing",
    "current",
    "unavailable",
    "failed",
]


@dataclass(frozen=True, slots=True)
class CodeGraphView:
    """The user-visible state for one Feature architecture index."""

    status: CodeGraphStatus
    target_commit: str | None = None
    indexed_commit: str | None = None
    viewer_url: str | None = None
    message: str | None = None
