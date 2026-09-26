"""Background commit reconciliation; Workspace reads only cached projections."""

from collections.abc import Callable, Iterable
from dataclasses import replace
from threading import Event, Lock, Thread

from loguru import logger

from agentplanex.domains.workspace import FeatureBinding
from agentplanex.infrastructure.git_repository import GitRepository
from agentplanex.infrastructure.gitnexus import GitNexusClient, GitNexusUnavailable
from agentplanex.services.code_graph.models import CodeGraphView


class CodeGraphService:
    """Serialize optional analysis outside Runtime and request threads."""

    def __init__(
        self,
        *,
        client: GitNexusClient,
        bindings: Callable[[], Iterable[FeatureBinding]],
        enabled: bool = True,
        poll_interval_seconds: float = 5.0,
    ) -> None:
        self.client = client
        self.bindings = bindings
        self.enabled = enabled
        self.interval = poll_interval_seconds
        self._views: dict[tuple[str, str], CodeGraphView] = {}
        self._unverified_indexes: set[tuple[str, str]] = set()
        self._guard = Lock()
        self._stop = Event()
        self._thread: Thread | None = None

    def start(self) -> None:
        if self.enabled and self._thread is None:
            self._stop.clear()
            self._thread = Thread(target=self._run, name="code-graph", daemon=True)
            self._thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
            self._thread = None

    def view(self, binding: FeatureBinding) -> CodeGraphView:
        if not self.enabled:
            return CodeGraphView(status="disabled")
        with self._guard:
            return self._views.get(
                (binding.project_id, binding.triage_id),
                CodeGraphView(status="pending", message="Waiting for architecture analysis."),
            )

    def _publish(self, binding: FeatureBinding, view: CodeGraphView) -> None:
        with self._guard:
            self._views[binding.project_id, binding.triage_id] = view

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                bindings = tuple(self.bindings())
                keys = {(b.project_id, b.triage_id) for b in bindings}
                self._unverified_indexes.intersection_update(keys)
                with self._guard:
                    self._views = {k: v for k, v in self._views.items() if k in keys}
                for binding in bindings:
                    if self._stop.is_set():
                        break
                    self._refresh(binding)
            except Exception:
                logger.exception("Code Graph reconciliation failed; retrying")
            self._stop.wait(self.interval)

    def _refresh(self, binding: FeatureBinding) -> None:
        key = (binding.project_id, binding.triage_id)
        view = self.view(binding)
        try:
            git = GitRepository(binding.worktree_path)
            head = git.head_sha()
            view = replace(view, target_commit=head)
            if git.changed_paths():
                self._publish(binding, replace(
                    view, status="waiting_clean",
                    message="Uncommitted changes are excluded. Waiting for a clean worktree.",
                ))
                return
            info = self.client.repository(binding.worktree_path)
            if (
                info is not None
                and info.last_commit == head
                and key not in self._unverified_indexes
            ):
                self._publish(binding, CodeGraphView(
                    status="current", target_commit=head, indexed_commit=head,
                    viewer_url=self.client.viewer_url(binding.worktree_path),
                ))
                return
            view = CodeGraphView(
                status="analyzing", target_commit=head,
                indexed_commit=info.last_commit if info else None,
                message="Updating the committed-code architecture view.",
            )
            self._publish(binding, view)
            # A matching commit alone cannot validate an interrupted or rejected analysis.
            self._unverified_indexes.add(key)
            self.client.analyze(binding.worktree_path, self._stop)
            # A changed checkout cannot be advertised as a graph of the captured commit.
            if git.head_sha() != head or git.changed_paths():
                raise RuntimeError("Worktree changed during analysis; retrying the latest commit.")
            info = self.client.repository(binding.worktree_path)
            if info is None or info.last_commit != head:
                raise RuntimeError("GitNexus did not publish the requested commit.")
            self._unverified_indexes.discard(key)
            self._publish(binding, replace(
                view, status="current", indexed_commit=head, message=None,
                viewer_url=self.client.viewer_url(binding.worktree_path),
            ))
        except Exception as error:
            self._publish(binding, replace(
                view,
                status="unavailable" if isinstance(error, GitNexusUnavailable) else "failed",
                viewer_url=None, message=str(error)[:400],
            ))
