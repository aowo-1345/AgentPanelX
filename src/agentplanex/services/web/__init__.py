"""Read-only service projections consumed by the Web application."""

from agentplanex.services.web.project_workspace import (
    PlanDocument,
    ProjectWorkspaceQuery,
    ProjectWorkspaceView,
    ToolActivity,
    VisibleMessage,
)

__all__ = [
    "PlanDocument",
    "ProjectWorkspaceQuery",
    "ProjectWorkspaceView",
    "ToolActivity",
    "VisibleMessage",
]
