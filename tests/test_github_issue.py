import json
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from agentplanex.infrastructure.github_issue import GitHubIssuePublisher


def _git(repository_path: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(repository_path), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )


@dataclass(slots=True)
class _RecordedRequest:
    path: str
    authorization: str | None
    payload: object


@contextmanager
def _github_endpoint() -> Iterator[tuple[str, list[_RecordedRequest]]]:
    requests: list[_RecordedRequest] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers["Content-Length"])
            requests.append(
                _RecordedRequest(
                    path=self.path,
                    authorization=self.headers.get("Authorization"),
                    payload=json.loads(self.rfile.read(length)),
                )
            )
            body = json.dumps(
                {
                    "number": 42,
                    "html_url": "https://github.com/octocat/hello-world/issues/42",
                }
            ).encode()
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_create_issue_uses_local_token_and_agentpanelx_repository_origin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    managed_repository_path = tmp_path / "managed-repository"
    managed_repository_path.mkdir()
    _git(managed_repository_path, "init", "--initial-branch=main")
    issue_repository_path = tmp_path / "agentpanelx-repository"
    issue_repository_path.mkdir()
    _git(issue_repository_path, "init", "--initial-branch=main")
    _git(
        issue_repository_path,
        "remote",
        "add",
        "origin",
        "git@github-work:octocat/hello-world.git",
    )
    data_home = tmp_path / ".agentplanex"
    token_path = data_home / "secrets" / "github" / "token"
    token_path.parent.mkdir(parents=True)
    token_path.write_text("local-token\n", encoding="utf-8")

    with _github_endpoint() as (api_base_url, requests):
        issue = GitHubIssuePublisher(
            data_home=data_home,
            issue_repository_path=issue_repository_path,
            api_base_url=api_base_url,
        ).create(
            repository_path=managed_repository_path,
            title="[AgentPanelX] Repair delivery",
            body="# Proposal\n\nFix the actual cause.\n",
        )

    assert issue.number == 42
    assert issue.url == "https://github.com/octocat/hello-world/issues/42"
    assert requests == [
        _RecordedRequest(
            path="/repos/octocat/hello-world/issues",
            authorization="Bearer local-token",
            payload={
                "title": "[AgentPanelX] Repair delivery",
                "body": "# Proposal\n\nFix the actual cause.\n",
            },
        )
    ]
