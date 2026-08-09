"""FastAPI host over the existing Workspace and Project Runtime services."""

import argparse
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, status
from fastapi.middleware.cors import CORSMiddleware

from agentplanex.bootstrap import create_workspace
from agentplanex.services.workspace import WorkspaceService
from agentplanex.services.workspace_worker import WorkspaceWorker
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


def create_app(settings: Settings) -> FastAPI:
    workspace = create_workspace(settings)
    worker = WorkspaceWorker(workspace)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        worker.start()
        try:
            yield
        finally:
            worker.close()

    app = FastAPI(title="AgentPlaneX", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^http://(localhost|127\.0\.0\.1)(:\d+)?$",
        allow_methods=["*"],
        allow_headers=["*"],
    )
    install_error_handlers(app)
    _install_routes(app, workspace, worker)
    return app


def _install_routes(
    app: FastAPI,
    workspace: WorkspaceService,
    worker: WorkspaceWorker,
) -> None:
    @app.get("/api/projects", response_model=list[ProjectResponse])
    def list_projects() -> list[ProjectResponse]:
        return [project_response(item) for item in workspace.list_projects()]

    @app.post(
        "/api/projects",
        response_model=ProjectResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def create_project(request: CreateProjectRequest) -> ProjectResponse:
        return project_response(
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
        worker.notify()
        return activation_response(activation)

    @app.post(
        "/api/projects/{project_id}/features/{triage_id}/actions",
        response_model=WorkspaceResponse,
    )
    def perform_action(
        project_id: str,
        triage_id: str,
        request: ActionRequest,
    ) -> WorkspaceResponse:
        result = workspace.perform_feature_action(
            project_id=project_id,
            triage_id=triage_id,
            action=request.action,
            feedback=request.feedback or "",
        )
        worker.notify()
        return workspace_response(result)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentplanex-web")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--host", choices=("127.0.0.1", "localhost"), default="127.0.0.1")
    parser.add_argument("--port", type=int, default=13475)
    args = parser.parse_args(argv)
    uvicorn.run(
        create_app(load_settings(args.config)),
        host=args.host,
        port=args.port,
    )
    return 0
