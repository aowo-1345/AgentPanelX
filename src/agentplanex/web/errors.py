"""Expected application errors translated to standard HTTP responses."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from agentplanex.infrastructure.workspace_git import WorkspaceGitError


def install_error_handlers(app: FastAPI) -> None:
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
