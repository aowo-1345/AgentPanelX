"""Expected application errors translated to standard HTTP responses."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from agentplanex.infrastructure.workspace_git import WorkspaceGitError
from agentplanex.project_runtime.errors import FeatureBusyError
from agentplanex.services.workspace.errors import WorkspaceCapacityExhaustedError


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(FeatureBusyError)
    async def feature_busy(
        _request: Request,
        error: FeatureBusyError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"detail": str(error), "code": error.code},
        )

    @app.exception_handler(WorkspaceCapacityExhaustedError)
    async def capacity_exhausted(
        _request: Request,
        error: WorkspaceCapacityExhaustedError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=429,
            content={"detail": str(error), "code": error.code},
        )

    @app.exception_handler(LookupError)
    async def not_found(_request: Request, error: LookupError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(error)})

    @app.exception_handler(ValueError)
    @app.exception_handler(WorkspaceGitError)
    async def invalid_request(
        _request: Request,
        error: ValueError | WorkspaceGitError,
    ) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(error)})
