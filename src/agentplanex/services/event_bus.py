"""Synchronous in-process event distribution."""

from collections.abc import Callable
from dataclasses import dataclass

from loguru import logger

from agentplanex.domains.conversation_event import ConversationEvent
from agentplanex.domains.execution_event import ExecutionEvent

type DomainEvent = ExecutionEvent | ConversationEvent
type EventHandler = Callable[[ExecutionEvent], None]
type SubscriptionHandler = Callable[[object], None]


@dataclass(frozen=True, slots=True)
class EventSubscription:
    """Route one event class to one handler."""

    event_type: type[DomainEvent]
    handler: SubscriptionHandler


@dataclass(frozen=True, slots=True)
class EventBus:
    handlers: tuple[EventHandler, ...] = ()
    subscriptions: tuple[EventSubscription, ...] = ()

    def publish(self, event: DomainEvent) -> None:
        """Synchronously notify handlers without changing business outcomes."""
        if isinstance(event, ExecutionEvent):
            for handler in self.handlers:
                try:
                    handler(event)
                except Exception:
                    logger.exception(
                        "Execution event handler failed triage_id={} event_type={}",
                        event.triage_id,
                        event.event_type.value,
                    )
        for subscription in self.subscriptions:
            if not isinstance(event, subscription.event_type):
                continue
            try:
                subscription.handler(event)
            except Exception:
                logger.exception(
                    "Event handler failed triage_id={} event_type={}",
                    event.triage_id,
                    type(event).__name__,
                )
