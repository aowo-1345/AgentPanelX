"""FastAPI host over the existing Workspace and Project Runtime services."""

import argparse
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from agentplanex.bootstrap import create_workspace
from agentplanex.domains import FeatureAction, ManagedProject
from agentplanex.services.workspace.service import WorkspaceService
from agentplanex.settings import Settings, load_settings
from agentplanex.web.errors import install_error_handlers
from agentplanex.web.schemas import (
    ActionRequest,
    ActivationResponse,
    BoardFeatureResponse,
    CreateFeatureRequest,
    CreateProjectRequest,
    FeatureResponse,
    MessageRequest,
    ProjectResponse,
    WorkspaceResponse,
    activation_response,
    board_feature_response,
    feature_response,
    project_response,
    workspace_response,
)


def create_app(settings: Settings, *, frontend_dist: Path | None = None) -> FastAPI:
    workspace = create_workspace(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        workspace.start()
        try:
            yield
        finally:
            workspace.close()

    app = FastAPI(title="AgentPlaneX", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^http://(localhost|127\.0\.0\.1)(:\d+)?$",
        allow_methods=["*"],
        allow_headers=["*"],
    )
    install_error_handlers(app)
    _install_routes(app, workspace)
    if frontend_dist is not None:
        _install_frontend(app, frontend_dist)
    return app


def _install_routes(
    app: FastAPI,
    workspace: WorkspaceService,
) -> None:
    def project_with_version(project: ManagedProject) -> ProjectResponse:
        return project_response(
            project,
            git_version=workspace.project_git_version(project),
        )

    @app.get("/api/projects", response_model=list[ProjectResponse])
    def list_projects() -> list[ProjectResponse]:
        return [project_with_version(item) for item in workspace.list_projects()]

    @app.post("/api/projects/refresh", response_model=list[ProjectResponse])
    def refresh_projects() -> list[ProjectResponse]:
        return [project_with_version(item) for item in workspace.refresh_projects()]

    @app.post(
        "/api/projects",
        response_model=ProjectResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def create_project(request: CreateProjectRequest) -> ProjectResponse:
        return project_with_version(
            workspace.register_project(
                name=request.name,
                repository_path=Path(request.repository_path),
                main_branch=request.main_branch,
            )
        )

    @app.get("/api/features", response_model=list[BoardFeatureResponse])
    def list_features() -> list[BoardFeatureResponse]:
        return [
            board_feature_response(feature, board.name)
            for board in workspace.all_project_boards()
            for feature in board.features
        ]

    @app.post(
        "/api/projects/{project_id}/features",
        response_model=FeatureResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def create_feature(
        project_id: str,
        request: CreateFeatureRequest,
    ) -> FeatureResponse:
        return feature_response(
            workspace.create_feature(project_id=project_id, name=request.name)
        )

    @app.delete(
        "/api/projects/{project_id}/features/{triage_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    def delete_feature(project_id: str, triage_id: str) -> None:
        workspace.delete_feature(project_id=project_id, triage_id=triage_id)

    @app.get(
        "/api/projects/{project_id}/features/{triage_id}/workspace",
        response_model=WorkspaceResponse,
    )
    def get_workspace(project_id: str, triage_id: str) -> WorkspaceResponse:
        return workspace_response(
            workspace.feature_workspace(
                project_id=project_id,
                triage_id=triage_id,
            )
        )

    @app.post(
        "/api/projects/{project_id}/features/{triage_id}/messages",
        response_model=ActivationResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def send_message(
        project_id: str,
        triage_id: str,
        request: MessageRequest,
    ) -> ActivationResponse:
        activation = workspace.submit_feature_message(
            project_id=project_id,
            triage_id=triage_id,
            content=request.content,
        )
        return activation_response(activation)

    @app.post(
        "/api/projects/{project_id}/features/{triage_id}/actions",
        response_model=WorkspaceResponse,
    )
    def perform_action(
        project_id: str,
        triage_id: str,
        request: ActionRequest,
        response: Response,
    ) -> WorkspaceResponse:
        result = workspace.perform_feature_action(
            project_id=project_id,
            triage_id=triage_id,
            action=request.action,
            feedback=request.feedback or "",
        )
        if request.action is not FeatureAction.BEGIN:
            response.status_code = status.HTTP_202_ACCEPTED
        return workspace_response(result)


def _install_frontend(app: FastAPI, frontend_dist: Path) -> None:
    """Serve one built SPA without changing or shadowing the API contract."""

    root = frontend_dist.resolve()
    index = root / "index.html"
    if not index.is_file():
        raise ValueError(f"Frontend build is missing index.html: {root}")

    @app.get("/{frontend_path:path}", include_in_schema=False)
    def frontend(frontend_path: str) -> FileResponse:
        if frontend_path == "api" or frontend_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="API route not found")
        candidate = (root / frontend_path).resolve()
        if candidate.is_relative_to(root) and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentplanex-web")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--host", choices=("127.0.0.1", "localhost"), default="127.0.0.1")
    parser.add_argument("--port", type=int, default=13475)
    parser.add_argument(
        "--frontend-dist",
        type=Path,
        help="serve a built Web Console directory (defaults to frontend/dist when present)",
    )
    args = parser.parse_args(argv)
    frontend_dist = args.frontend_dist
    default_frontend_dist = Path.cwd() / "frontend" / "dist"
    if frontend_dist is None and (default_frontend_dist / "index.html").is_file():
        frontend_dist = default_frontend_dist
    if frontend_dist is not None and not (frontend_dist / "index.html").is_file():
        parser.error(f"frontend build is missing index.html: {frontend_dist}")
    uvicorn.run(
        create_app(load_settings(args.config), frontend_dist=frontend_dist),
        host=args.host,
        port=args.port,
    )
    return 0
