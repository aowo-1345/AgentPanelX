"""Application-local fan-out for committed Project Owner conversation events."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from threading import Lock

from agentplanex.domains.conversation_event import (
    ConversationActivationUpdated,
    ConversationEvent,
    ConversationMessageAppended,
)


class ConversationSubscriber:
    """One browser connection with an isolated async queue."""

    def __init__(self, triage_id: str) -> None:
        self.triage_id = triage_id
        self._loop = asyncio.get_running_loop()
        self._queue: asyncio.Queue[ConversationEvent | None] = asyncio.Queue()
        self._closed = False
        self._lock = Lock()

    def publish(self, event: ConversationEvent) -> None:
        with self._lock:
            if self._closed:
                return
            self._loop.call_soon_threadsafe(self._queue.put_nowait, event)

    async def next(self) -> ConversationEvent | None:
        return await self._queue.get()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._loop.call_soon_threadsafe(self._queue.put_nowait, None)


class ConversationHub:
    """Broadcast conversation events to independent subscribers by Feature."""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[ConversationSubscriber]] = defaultdict(set)
        self._lock = Lock()

    def subscribe(self, triage_id: str) -> ConversationSubscriber:
        subscriber = ConversationSubscriber(triage_id)
        with self._lock:
            self._subscribers[triage_id].add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: ConversationSubscriber) -> None:
        subscriber.close()
        with self._lock:
            subscribers = self._subscribers.get(subscriber.triage_id)
            if subscribers is None:
                return
            subscribers.discard(subscriber)
            if not subscribers:
                del self._subscribers[subscriber.triage_id]

    def publish(self, event: object) -> None:
        if not isinstance(event, (ConversationMessageAppended, ConversationActivationUpdated)):
            return
        with self._lock:
            subscribers = tuple(self._subscribers.get(event.triage_id, ()))
        for subscriber in subscribers:
            subscriber.publish(event)

    def close(self) -> None:
        with self._lock:
            subscribers = tuple(
                subscriber
                for group in self._subscribers.values()
                for subscriber in group
            )
            self._subscribers.clear()
        for subscriber in subscribers:
            subscriber.close()
