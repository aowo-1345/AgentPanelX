"""Application logging must not change EventBus delivery behavior."""

from loguru import logger

from agentplanex.domains.execution_event import ExecutionEvent, ExecutionEventType
from agentplanex.services.event_bus import EventBus


def test_failed_event_handler_is_logged_with_context_and_delivery_continues() -> None:
    event = ExecutionEvent(
        triage_id="feature-a",
        event_type=ExecutionEventType.REACT_LOOP_ENTERED,
    )
    delivered: list[ExecutionEvent] = []
    messages: list[str] = []
    sink_id = logger.add(messages.append, format="{message}")

    def fail(_event: ExecutionEvent) -> None:
        raise RuntimeError("handler failure")

    try:
        EventBus(handlers=(fail, delivered.append)).publish(event)
    finally:
        logger.remove(sink_id)

    assert delivered == [event]
    assert any(
        "Execution event handler failed "
        "triage_id=feature-a event_type=REACT_LOOP_ENTERED" in message
        for message in messages
    )
