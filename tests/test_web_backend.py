"""One user-visible acceptance path through the installed Web backend."""

import json
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Lock, Thread
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pytest
import yaml

from agentplanex.settings import DEFAULT_SETTINGS_PATH, load_settings


def _git(project_path: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(project_path), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _prepare_case(model_base_url: str) -> tuple[Path, Path]:
    case_path = (
        Path(__file__).resolve().parents[1]
        / ".agentplanex"
        / "tests"
        / "web-e2e"
    )
    shutil.rmtree(case_path, ignore_errors=True)
    repository_path = case_path / "repository"
    repository_path.mkdir(parents=True)
    subprocess.run(
        ["git", "init", "--initial-branch=main", str(repository_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    _git(repository_path, "config", "user.name", "AgentPlaneX Tests")
    _git(repository_path, "config", "user.email", "tests@agentplanex.local")
    (repository_path / "README.md").write_text("# Web E2E\n", encoding="utf-8")
    _git(repository_path, "add", "README.md")
    _git(repository_path, "commit", "-m", "Initial commit")

    settings = load_settings(DEFAULT_SETTINGS_PATH).model_dump(mode="json")
    settings["workspace"] = {"data_home": str(case_path / "data-home")}
    settings["project_owner_agent"]["model"]["base_url"] = model_base_url
    config_path = case_path / "settings.yaml"
    config_path.write_text(yaml.safe_dump(settings), encoding="utf-8")
    return repository_path, config_path


@dataclass(slots=True)
class _ModelEndpoint:
    base_url: str
    second_request_started: Event
    release_second_request: Event


@contextmanager
def _model_endpoint() -> Iterator[_ModelEndpoint]:
    second_request_started = Event()
    release_second_request = Event()
    request_lock = Lock()
    request_count = 0

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            nonlocal request_count
            with request_lock:
                request_count += 1
                request_number = request_count
            if request_number == 2:
                second_request_started.set()
                release_second_request.wait(timeout=20)
            body = json.dumps(
                {
                    "id": "resp_web_e2e",
                    "object": "response",
                    "created_at": int(time.time()),
                    "status": "completed",
                    "model": "test-model",
                    "output": [
                        {
                            "id": "msg_web_e2e",
                            "type": "message",
                            "status": "completed",
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "The Project Owner is ready.",
                                    "annotations": [],
                                }
                            ],
                        }
                    ],
                    "parallel_tool_calls": False,
                    "tool_choice": "auto",
                    "tools": [],
                    "error": None,
                    "incomplete_details": None,
                    "instructions": None,
                    "metadata": {},
                    "temperature": 1.0,
                    "top_p": 1.0,
                    "usage": {
                        "input_tokens": 1,
                        "input_tokens_details": {"cached_tokens": 0},
                        "output_tokens": 1,
                        "output_tokens_details": {"reasoning_tokens": 0},
                        "total_tokens": 2,
                    },
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            with suppress(BrokenPipeError, ConnectionResetError):
                self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield _ModelEndpoint(
            base_url=f"http://127.0.0.1:{server.server_port}/v1",
            second_request_started=second_request_started,
            release_second_request=release_second_request,
        )
    finally:
        release_second_request.set()
        server.shutdown()
        server.server_close()
        thread.join()


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _request(
    base_url: str,
    method: str,
    path: str,
    payload: dict[str, object] | None = None,
) -> tuple[int, object]:
    body = json.dumps(payload).encode() if payload is not None else None
    request = Request(
        f"{base_url}{path}",
        data=body,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        with urlopen(request, timeout=2) as response:
            body = response.read()
            return response.status, json.loads(body) if body else None
    except HTTPError as error:
        return error.code, json.load(error)


@contextmanager
def _web_server(config_path: Path, port: int) -> Iterator[str]:
    executable = Path(sys.executable).with_name("agentplanex-web")
    process = subprocess.Popen(
        [
            str(executable),
            "--config",
            str(config_path),
            "--port",
            str(port),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                pytest.fail(f"agentplanex-web exited early:\n{stdout}\n{stderr}")
            try:
                status, _ = _request(base_url, "GET", "/api/projects")
            except (URLError, TimeoutError):
                time.sleep(0.05)
                continue
            if status == 200:
                break
        else:
            pytest.fail("agentplanex-web did not start")
        yield base_url
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


@pytest.mark.e2e
def test_installed_web_backend_runs_and_recovers_the_workspace() -> None:
    with _model_endpoint() as model_endpoint:
        repository_path, config_path = _prepare_case(model_endpoint.base_url)
        port = _free_port()

        with _web_server(config_path, port) as base_url:
            status, project = _request(
                base_url,
                "POST",
                "/api/projects",
                {
                    "name": "Web Project",
                    "repository_path": str(repository_path),
                    "main_branch": "main",
                },
            )
            assert status == 201
            assert isinstance(project, dict)
            project_id = project["project_id"]
            assert _request(base_url, "GET", "/api/projects") == (
                200,
                [project],
            )

            status, feature = _request(
                base_url,
                "POST",
                f"/api/projects/{project_id}/features",
                {"name": "Web Feature"},
            )
            assert status == 201
            assert isinstance(feature, dict)
            triage_id = feature["triage_id"]
            feature_path = Path(feature["worktree_path"])

            status, disposable = _request(
                base_url,
                "POST",
                f"/api/projects/{project_id}/features",
                {"name": "Disposable Feature"},
            )
            assert status == 201
            assert isinstance(disposable, dict)
            disposable_path = Path(disposable["worktree_path"])
            disposable_branch = disposable["branch"]
            assert _request(
                base_url,
                "DELETE",
                f"/api/projects/{project_id}/features/{disposable['triage_id']}",
            ) == (204, None)
            assert not disposable_path.exists()
            assert _git(repository_path, "rev-parse", "--verify", disposable_branch)

            for document_name, content in (
                ("architecture.md", "# Architecture\n\nHTTP stays an adapter.\n"),
                ("requirements.md", "# Requirements\n\nExpose the workspace.\n"),
                ("roadmap.md", "# Roadmap\n\nShip the backend.\n"),
            ):
                (feature_path / document_name).write_text(content, encoding="utf-8")

            status, refused = _request(
                base_url,
                "DELETE",
                f"/api/projects/{project_id}/features/{triage_id}",
            )
            assert status == 400
            assert isinstance(refused, dict)
            assert "worktree remove" in refused["detail"]
            assert feature_path.exists()

            status, board = _request(base_url, "GET", "/api/features")
            assert status == 200
            assert isinstance(board, list)
            assert board == [
                {
                    "project_id": project_id,
                    "project_name": "Web Project",
                    "triage_id": triage_id,
                    "name": "Web Feature",
                    "status": "TRIAGE",
                    "branch": feature["branch"],
                    "pending_action": None,
                    "current_milestone_key": None,
                    "current_stage_key": None,
                }
            ]

            assert _request(
                base_url,
                "POST",
                f"/api/projects/{project_id}/features/{triage_id}/actions",
                {"action": "begin"},
            )[0] == 200
            status, board = _request(base_url, "GET", "/api/features")
            assert status == 200
            assert isinstance(board, list)
            assert board[0]["status"] == "TODO"

            status, accepted = _request(
                base_url,
                "POST",
                f"/api/projects/{project_id}/features/{triage_id}/messages",
                {"content": "What should we do next?"},
            )
            assert status == 202
            assert isinstance(accepted, dict)
            assert accepted["status"] == "PENDING"

            workspace_path = (
                f"/api/projects/{project_id}/features/{triage_id}/workspace"
            )
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                status, workspace = _request(base_url, "GET", workspace_path)
                assert status == 200
                assert isinstance(workspace, dict)
                messages = workspace["conversation"]["data"]
                if any(
                    message["role"] == "assistant"
                    and message["content"] == "The Project Owner is ready."
                    for message in messages
                ):
                    break
                time.sleep(0.05)
            else:
                pytest.fail("Workspace did not expose the Project Owner reply")
            assert [message["role"] for message in messages] == ["user", "assistant"]
            assert [
                document["content"] for document in workspace["plan"]["data"]["documents"]
            ] == [
                "# Architecture\n\nHTTP stays an adapter.\n",
                "# Requirements\n\nExpose the workspace.\n",
                "# Roadmap\n\nShip the backend.\n",
            ]
            assert workspace["git"]["data"]["branch"] == feature["branch"]

            assert _request(
                base_url,
                "POST",
                f"/api/projects/{project_id}/features/{triage_id}/actions",
                {"action": "reject-plan", "feedback": "  "},
            )[0] == 422

            assert _request(
                base_url,
                "POST",
                f"/api/projects/{project_id}/features/{triage_id}/messages",
                {"content": "This activation will be interrupted."},
            )[0] == 202
            assert model_endpoint.second_request_started.wait(timeout=5)
            status, interrupted_workspace = _request(
                base_url,
                "GET",
                workspace_path,
            )
            assert status == 200
            assert isinstance(interrupted_workspace, dict)
            assert interrupted_workspace["runtime"]["data"]["activation_status"] == (
                "RUNNING"
            )
            status, active_delete = _request(
                base_url,
                "DELETE",
                f"/api/projects/{project_id}/features/{triage_id}",
            )
            assert status == 400
            assert isinstance(active_delete, dict)
            assert "being processed" in active_delete["detail"]

            status, pending_feature = _request(
                base_url,
                "POST",
                f"/api/projects/{project_id}/features",
                {"name": "Pending Feature"},
            )
            assert status == 201
            assert isinstance(pending_feature, dict)
            pending_triage_id = pending_feature["triage_id"]
            pending_workspace_path = (
                f"/api/projects/{project_id}/features/"
                f"{pending_triage_id}/workspace"
            )
            assert _request(
                base_url,
                "POST",
                f"/api/projects/{project_id}/features/{pending_triage_id}/actions",
                {"action": "begin"},
            )[0] == 200
            assert _request(
                base_url,
                "POST",
                f"/api/projects/{project_id}/features/{pending_triage_id}/messages",
                {"content": "Resume this after restart."},
            )[0] == 202
            status, pending_workspace = _request(
                base_url,
                "GET",
                pending_workspace_path,
            )
            assert status == 200
            assert isinstance(pending_workspace, dict)
            assert pending_workspace["runtime"]["data"]["activation_status"] == "PENDING"

        model_endpoint.release_second_request.set()
        with _web_server(config_path, port) as base_url:
            assert _request(base_url, "GET", "/api/projects")[1] == [project]
            status, restored = _request(base_url, "GET", workspace_path)
            assert status == 200
            assert isinstance(restored, dict)
            assert restored["feature"]["triage_id"] == triage_id
            assert restored["conversation"]["data"][:2] == messages
            assert any(
                entry["role"] == "status"
                and "stopped while this activation was running" in entry["content"]
                for entry in restored["conversation"]["data"]
            )

            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                status, pending_workspace = _request(
                    base_url,
                    "GET",
                    pending_workspace_path,
                )
                assert status == 200
                assert isinstance(pending_workspace, dict)
                pending_messages = pending_workspace["conversation"]["data"]
                if any(
                    entry["role"] == "assistant"
                    and entry["content"] == "The Project Owner is ready."
                    for entry in pending_messages
                ):
                    break
                time.sleep(0.05)
            else:
                pytest.fail("Restarted Worker did not resume the pending activation")
