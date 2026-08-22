"""Synchronous in-process event distribution."""

import logging
from collections.abc import Callable
from dataclasses import dataclass

from agentplanex.domains.execution_event import ExecutionEvent

logger = logging.getLogger(__name__)

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
                    "Execution event handler failed",
                    extra={
                        "triage_id": event.triage_id,
                        "event_type": event.event_type.value,
                    },
                )
