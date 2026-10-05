"""Small Web/CLI entry adapters with explicit close boundaries."""

from __future__ import annotations

import asyncio
import contextlib
import io
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass(slots=True)
class CliHarness:
    """Invoke a CLI entry point without creating a Runtime implicitly."""

    entrypoint: Callable[[list[str]], object]
    _closed: bool = False

    def run(self, *argv: str) -> ProcessResult:
        if self._closed:
            raise RuntimeError("CLI harness is closed")
        stdout = io.StringIO()
        stderr = io.StringIO()
        returncode = 0
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                result = self.entrypoint(list(argv))
                if isinstance(result, int):
                    returncode = result
            except SystemExit as error:
                if error.code is None:
                    returncode = 0
                elif isinstance(error.code, int):
                    returncode = error.code
                else:
                    returncode = 1
                    print(error.code, file=stderr)
        return ProcessResult(returncode, stdout.getvalue(), stderr.getvalue())

    def close(self) -> None:
        self._closed = True


@dataclass(slots=True)
class WebHarness:
    """Issue an ASGI request with a per-request, closed client."""

    app: Any
    base_url: str = "http://testserver"
    _closed: bool = False

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        if self._closed:
            raise RuntimeError("Web harness is closed")

        async def send() -> Any:
            import httpx

            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(transport=transport, base_url=self.base_url) as client:
                return await client.request(method, path, **kwargs)

        return asyncio.run(send())

    def close(self) -> None:
        self._closed = True
