"""One serial background driver for Web-managed Feature Runtimes."""

from dataclasses import dataclass, field
from threading import Event, Thread

from agentplanex.services.workspace import WorkspaceService


@dataclass(slots=True)
class WorkspaceWorker:
    """Wake on commands and consume machine-owned Runtime steps serially."""

    workspace: WorkspaceService
    _wake: Event = field(default_factory=Event, init=False)
    _stop: Event = field(default_factory=Event, init=False)
    _thread: Thread | None = field(default=None, init=False)

    def start(self) -> None:
        self.workspace.fail_interrupted_work()
        self._thread = Thread(target=self._run, name="agentplanex-worker", daemon=True)
        self._thread.start()
        self.notify()

    def notify(self) -> None:
        self._wake.set()

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join()

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait()
            self._wake.clear()
            while not self._stop.is_set():
                if not self.workspace.drive_next_automatic_step():
                    break
