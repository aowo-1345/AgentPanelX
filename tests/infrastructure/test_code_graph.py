"""Code Graph coordination at the Feature/Workspace boundary."""

from __future__ import annotations

import subprocess
from pathlib import Path
from threading import Event
from time import monotonic, sleep

from agentplanex.domains.workspace import FeatureBinding
from agentplanex.infrastructure.gitnexus import GitNexusRepository, GitNexusUnavailable
from agentplanex.services.code_graph import CodeGraphService


class _FakeGitNexus:
    def __init__(self, *, unavailable: bool = False) -> None:
        self.unavailable = unavailable
        self.last_commits: dict[Path, str] = {}
        self.analyze_calls: list[Path] = []

    def repository(self, path: Path) -> GitNexusRepository | None:
        if self.unavailable:
            raise GitNexusUnavailable("fake GitNexus is unavailable")
        commit = self.last_commits.get(path.resolve())
        return None if commit is None else GitNexusRepository(path.resolve(), commit)

    def analyze(self, path: Path, _stop: Event) -> None:
        resolved = path.resolve()
        self.analyze_calls.append(resolved)
        self.last_commits[resolved] = _git(resolved, "rev-parse", "HEAD")

    def viewer_url(self, path: Path) -> str:
        return f"http://127.0.0.1:4747/?repo={path.resolve()}"


def test_clean_commit_is_analyzed_once_and_dirty_changes_wait(tmp_path: Path) -> None:
    repository = _git_repository(tmp_path / "feature")
    binding = _binding(repository)
    client = _FakeGitNexus()
    service = CodeGraphService(
        client=client,
        bindings=lambda: (binding,),
        poll_interval_seconds=0.01,
    )
    service.start()
    try:
        assert _wait_for(lambda: service.view(binding).status == "current")
        assert len(client.analyze_calls) == 1

        (repository / "module.py").write_text("changed = True\n", encoding="utf-8")
        assert _wait_for(lambda: service.view(binding).status == "waiting_clean")
        assert len(client.analyze_calls) == 1

        _git(repository, "add", "module.py")
        _git(repository, "commit", "-m", "advance feature")
        assert _wait_for(lambda: len(client.analyze_calls) == 2)
        assert _wait_for(lambda: service.view(binding).status == "current")

        sleep(0.03)
        assert len(client.analyze_calls) == 2
    finally:
        service.close()


def test_rejected_index_is_reanalyzed_after_restoring_same_commit(tmp_path: Path) -> None:
    repository = _git_repository(tmp_path / "feature")
    binding = _binding(repository)
    original = (repository / "module.py").read_text()
    head = _git(repository, "rev-parse", "HEAD")

    class EditingGitNexus(_FakeGitNexus):
        def analyze(self, path: Path, stop: Event) -> None:
            super().analyze(path, stop)
            if len(self.analyze_calls) == 1:
                (path / "module.py").write_text("uncommitted = True\n")

    client = EditingGitNexus()
    service = CodeGraphService(
        client=client, bindings=lambda: (binding,), poll_interval_seconds=0.01,
    )
    service.start()
    try:
        assert _wait_for(lambda: service.view(binding).status == "waiting_clean")
        assert client.last_commits[repository.resolve()] == head
        assert len(client.analyze_calls) == 1
        (repository / "module.py").write_text(original)
        assert _wait_for(lambda: service.view(binding).status == "current")
        assert len(client.analyze_calls) == 2
        assert service.view(binding).indexed_commit == head
    finally:
        service.close()


def test_gitnexus_failure_is_projected_without_raising(tmp_path: Path) -> None:
    repository = _git_repository(tmp_path / "feature")
    binding = _binding(repository)
    service = CodeGraphService(
        client=_FakeGitNexus(unavailable=True),
        bindings=lambda: (binding,),
        poll_interval_seconds=0.01,
    )
    service.start()
    try:
        assert _wait_for(lambda: service.view(binding).status == "unavailable")
        assert "unavailable" in (service.view(binding).message or "")
    finally:
        service.close()


def _binding(path: Path) -> FeatureBinding:
    return FeatureBinding(
        project_id="project", triage_id="feature", name="Feature", worktree_path=path
    )


def _git_repository(path: Path) -> Path:
    path.mkdir()
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.email", "tests@example.test")
    _git(path, "config", "user.name", "Tests")
    (path / "module.py").write_text("value = 1\n", encoding="utf-8")
    _git(path, "add", "module.py")
    _git(path, "commit", "-m", "initial")
    return path


def _git(path: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), *arguments], check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def _wait_for(predicate: object, timeout: float = 2.0) -> bool:
    assert callable(predicate)
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if predicate():
            return True
        sleep(0.01)
    return bool(predicate())


def test_features_keep_independent_indexes_and_reuse_them_after_restart(tmp_path: Path) -> None:
    from dataclasses import replace

    first = _binding(_git_repository(tmp_path / 'first'))
    second = replace(_binding(_git_repository(tmp_path / 'second')), project_id='other')
    client = _FakeGitNexus()
    for _ in range(2):
        service = CodeGraphService(
            client=client, bindings=lambda: (first, second), poll_interval_seconds=0.01,
        )
        service.start()
        try:
            assert _wait_for(lambda service=service: service.view(second).status == 'current')
            assert service.view(first).viewer_url != service.view(second).viewer_url
            assert client.analyze_calls == [first.worktree_path, second.worktree_path]
        finally:
            service.close()


def test_http_adapter_uses_native_job_contract_and_rejects_sibling_index(tmp_path: Path) -> None:
    import json
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    from urllib.parse import parse_qs, urlsplit

    import pytest

    from agentplanex.infrastructure.gitnexus import GitNexusClient

    requested: list[dict[str, object]] = []
    repo_path = str(tmp_path)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            requested.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            self.respond({'jobId': 'analysis-1', 'status': 'cloning'})

        def do_GET(self) -> None:
            if self.path == '/api/analyze/analysis-1':
                self.respond({'status': 'complete'})
            else:
                assert parse_qs(urlsplit(self.path).query)['repo'] == [str(tmp_path)]
                self.respond({'repoPath': repo_path, 'lastCommit': 'commit'})

        def respond(self, data: dict[str, object]) -> None:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(data).encode())

        def log_message(self, *args: object) -> None:
            pass

    with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
        thread = Thread(target=server.serve_forever)
        thread.start()
        try:
            client = GitNexusClient(
                base_url=f'http://127.0.0.1:{server.server_port}',
                viewer_url='http://127.0.0.1:4173',
            )
            client.analyze(tmp_path, Event())
            assert requested == [{'path': str(tmp_path), 'force': True}]
            assert client.repository(tmp_path).last_commit == 'commit'
            assert parse_qs(urlsplit(client.viewer_url(tmp_path)).query) == {
                'server': [client.base_url], 'repo': [str(tmp_path)],
            }
            repo_path = str(tmp_path / 'sibling')
            with pytest.raises(GitNexusUnavailable, match='different worktree'):
                client.repository(tmp_path)
        finally:
            server.shutdown()
            thread.join()
