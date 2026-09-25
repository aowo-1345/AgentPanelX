"""Application logging must not change EventBus delivery behavior."""

from loguru import logger

from agentplanex.domains.conversation_event import ConversationMessageAppended
from agentplanex.domains.execution_event import ExecutionEvent, ExecutionEventType
from agentplanex.project_owner_agent.context.models import MessageHistory
from agentplanex.services.event_bus import EventBus, EventSubscription


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


def test_subscriptions_route_conversation_events_without_timeline_handlers() -> None:
    execution: list[ExecutionEvent] = []
    conversation: list[ConversationMessageAppended] = []
    event = ConversationMessageAppended(
        triage_id="feature-a",
        history=MessageHistory(
            project_owner_session_id="session-1",
            message_id="history-1",
            sequence=1,
            message=({"role": "assistant", "content": "hello"},),
        ),
    )

    EventBus(
        handlers=(execution.append,),
        subscriptions=(EventSubscription(ConversationMessageAppended, conversation.append),),
    ).publish(event)

    assert execution == []
    assert conversation == [event]
