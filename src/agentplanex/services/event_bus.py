"""Synchronous in-process event distribution."""

from collections.abc import Callable
from dataclasses import dataclass

from loguru import logger

from agentplanex.domains.execution_event import ExecutionEvent

type EventHandler = Callable[[ExecutionEvent], None]


@dataclass(frozen=True, slots=True)
class EventBus:
    handlers: tuple[EventHandler, ...] = ()

    def publish(self, event: ExecutionEvent) -> None:
        """Synchronously notify handlers without changing business outcomes."""
        for handler in self.handlers:
            try:
                handler(event)
            except Exception:
                logger.exception(
                    "Execution event handler failed triage_id={} event_type={}",
                    event.triage_id,
                    event.event_type.value,
                )
