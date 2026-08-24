"""In-process admission and execution for Feature-scoped Runtime work."""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from threading import Lock

from loguru import logger

from agentplanex.project_runtime.errors import FeatureBusyError
from agentplanex.services.workspace.errors import WorkspaceCapacityExhaustedError


@dataclass(slots=True)
class WorkspaceDispatcher:
    """Admit bounded automatic work while keeping each Feature exclusive."""

    max_parallel_features: int
    _guard: Lock = field(default_factory=Lock, init=False, repr=False)
    _busy_features: set[str] = field(default_factory=set, init=False, repr=False)
    _automatic_features: set[str] = field(default_factory=set, init=False, repr=False)
    _closed: bool = field(default=False, init=False, repr=False)
    _executor: ThreadPoolExecutor = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.max_parallel_features <= 0:
            raise ValueError("Workspace parallel Feature limit must be positive")
        self._executor = ThreadPoolExecutor(
            max_workers=self.max_parallel_features,
            thread_name_prefix="agentplanex-feature",
        )

    def dispatch[T](
        self,
        triage_id: str,
        *,
        persist: Callable[[], T],
        drive: Callable[[], object],
        after_release: Callable[[], None] | None = None,
    ) -> T:
        """Reserve first, persist synchronously, then drive in the background."""
        self._admit_automatic(triage_id)
        try:
            result = persist()
            self._executor.submit(
                self._drive_and_release,
                triage_id,
                drive,
                after_release,
            )
        except BaseException:
            self._release(triage_id)
            raise
        return result

    def exclusive[T](self, triage_id: str, command: Callable[[], T]) -> T:
        """Run a synchronous same-Feature command without consuming capacity."""
        self._admit_exclusive(triage_id)
        try:
            return command()
        finally:
            self._release(triage_id)

    def close(self) -> None:
        self.stop_accepting()
        self._executor.shutdown(wait=True, cancel_futures=True)

    def stop_accepting(self) -> None:
        """Reject new work before shutdown starts draining active work."""
        with self._guard:
            self._closed = True

    def _admit_automatic(self, triage_id: str) -> None:
        with self._guard:
            self._assert_open()
            if triage_id in self._busy_features:
                raise FeatureBusyError(triage_id)
            if len(self._automatic_features) >= self.max_parallel_features:
                raise WorkspaceCapacityExhaustedError(self.max_parallel_features)
            self._busy_features.add(triage_id)
            self._automatic_features.add(triage_id)

    def _admit_exclusive(self, triage_id: str) -> None:
        with self._guard:
            self._assert_open()
            if triage_id in self._busy_features:
                raise FeatureBusyError(triage_id)
            self._busy_features.add(triage_id)

    def _drive_and_release(
        self,
        triage_id: str,
        drive: Callable[[], object],
        after_release: Callable[[], None] | None,
    ) -> None:
        failure: BaseException | None = None
        try:
            drive()
        except BaseException as error:
            failure = error
            logger.exception("Feature Runtime drive failed for {}", triage_id)
        finally:
            self._release(triage_id)
        if after_release is not None:
            try:
                after_release()
            except BaseException:
                logger.exception("Feature post-release hook failed for {}", triage_id)
        if failure is not None:
            raise failure

    def _release(self, triage_id: str) -> None:
        with self._guard:
            self._busy_features.discard(triage_id)
            self._automatic_features.discard(triage_id)

    def _assert_open(self) -> None:
        if self._closed:
            raise RuntimeError("Workspace Dispatcher is closed")
