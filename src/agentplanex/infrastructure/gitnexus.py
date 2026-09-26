"""HTTP access to an independently operated GitNexus service."""

import json
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from time import monotonic
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


class GitNexusUnavailable(RuntimeError):
    """The optional GitNexus service is unavailable."""


@dataclass(frozen=True)
class GitNexusRepository:
    path: Path
    last_commit: str | None


class GitNexusClient:
    """Use GitNexus's repository registry and asynchronous analysis API."""

    def __init__(
        self, *, base_url: str, viewer_url: str | None = None,
        analysis_timeout_seconds: float = 1800,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.viewer_base_url = (viewer_url or base_url).rstrip("/")
        self.timeout = analysis_timeout_seconds

    def _request(
        self, route: str, *, method: str = "GET", payload: dict[str, object] | None = None,
    ) -> dict[str, object]:
        request = Request(
            self.base_url + route, method=method,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=5) as response:
                data = json.load(response)
        except HTTPError:
            raise
        except (OSError, ValueError) as error:
            raise GitNexusUnavailable(
                "Cannot reach GitNexus; check its configured service URL."
            ) from error
        if not isinstance(data, dict):
            raise GitNexusUnavailable("GitNexus returned an invalid response.")
        return data

    def repository(self, path: Path) -> GitNexusRepository | None:
        route = f"/api/repo?repo={quote(str(path.resolve()), safe='')}&awaitAnalysis=false"
        try:
            data = self._request(route)
        except HTTPError as error:
            if error.code == 404:
                return None
            raise GitNexusUnavailable(f"GitNexus lookup returned HTTP {error.code}.") from error
        # Never accept fallback resolution to a sibling worktree's index.
        if data.get("repoPath") != str(path.resolve()):
            raise GitNexusUnavailable("GitNexus resolved a different worktree; check its mounts.")
        commit = data.get("lastCommit")
        return GitNexusRepository(path.resolve(), commit if isinstance(commit, str) else None)

    def analyze(self, path: Path, stop: Event) -> None:
        job = self._request("/api/analyze", method="POST", payload={
            "path": str(path.resolve()), "force": True,
        })
        job_id = job.get("jobId")
        if not isinstance(job_id, str) or not job_id:
            raise GitNexusUnavailable("GitNexus did not return an analysis job ID.")
        route = f"/api/analyze/{quote(job_id, safe='')}"
        deadline = monotonic() + self.timeout
        while not stop.is_set() and monotonic() < deadline:
            job = self._request(route)
            if job.get("status") == "complete":
                return
            if job.get("status") in ("failed", "cancelled"):
                raise RuntimeError(str(job.get("error") or "GitNexus analysis failed.")[:400])
            stop.wait(1)
        # Stopping AgentPanelX leaves the externally owned service/job running.
        # GitNexus deduplicates the next request for this same repository.
        raise RuntimeError("Stopped waiting for GitNexus analysis; it may still be running.")

    def viewer_url(self, path: Path) -> str:
        query = urlencode({"server": self.base_url, "repo": str(path.resolve())})
        return f"{self.viewer_base_url}/?{query}"
