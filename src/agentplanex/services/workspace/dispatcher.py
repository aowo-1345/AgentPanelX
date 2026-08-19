"""In-process admission and execution for Feature-scoped Runtime work."""

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from threading import Lock

from agentplanex.services.workspace.errors import (
    FeatureBusyError,
    WorkspaceCapacityExhaustedError,
)

_LOGGER = logging.getLogger(__name__)


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
    ) -> T:
        """Reserve first, persist synchronously, then drive in the background."""
        self._admit_automatic(triage_id)
        try:
            result = persist()
            self._executor.submit(self._drive_and_release, triage_id, drive)
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
        with self._guard:
            self._closed = True
        self._executor.shutdown(wait=True)

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
    ) -> None:
        try:
            drive()
        except BaseException:
            _LOGGER.exception("Feature Runtime drive failed for %s", triage_id)
            raise
        finally:
            self._release(triage_id)

    def _release(self, triage_id: str) -> None:
        with self._guard:
            self._busy_features.discard(triage_id)
            self._automatic_features.discard(triage_id)

    def _assert_open(self) -> None:
        if self._closed:
            raise RuntimeError("Workspace Dispatcher is closed")
