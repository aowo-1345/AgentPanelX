"""High-value invariants for the Project Owner response stream."""

from agentplanex.domains.conversation_event import (
    ConversationMessageAppended,
    ConversationResponseUpdated,
)
from agentplanex.project_owner_agent.context.models import MessageHistory
from agentplanex.project_owner_agent.models.responses import (
    ResponseEvent,
    ResponsesClient,
    ResponsesRequest,
)
from agentplanex.services.project_runtime_context.models import (
    OwnerActivation,
    ProjectOwnerTaskType,
)
from agentplanex.services.web.conversation_projection import ConversationProjection


class _Transport:
    def __init__(self) -> None:
        self.created = 0
        self.streamed = 0

    def create(self, _request: ResponsesRequest) -> object:
        self.created += 1
        return {
            "object": "response",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "done"}],
                }
            ],
        }

    def stream(self, _request: ResponsesRequest):
        self.streamed += 1
        yield ResponseEvent(type="text_delta", delta="do")
        yield ResponseEvent(
            type="tool_delta", call_id="call-1", name="bash", delta='{"command":'
        )
        yield ResponseEvent(
            type="completed",
            response={
                "object": "response",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "done"}],
                    }
                ],
            },
        )


class _FailingTransport:
    def create(self, _request: ResponsesRequest) -> object:
        raise AssertionError("stream mode must not call create")

    def stream(self, _request: ResponsesRequest):
        yield ResponseEvent(type="text_delta", delta="partial")
        raise TimeoutError("upstream timed out")


def _request() -> ResponsesRequest:
    return ResponsesRequest(
        model="test",
        instructions="Reply.",
        input=({"role": "user", "content": "hello"},),
        tools=(),
        tool_choice="none",
    )


def test_mode_is_snapshotted_and_stream_and_non_stream_share_lifecycle() -> None:
    transport = _Transport()
    events: list[ResponseEvent] = []

    def callback(_response_id: str, event: ResponseEvent) -> None:
        events.append(event)

    stream_client = ResponsesClient(
        model="test",
        transport=transport,
        response_mode="stream",
        stream_callback=callback,
    )

    stream_client.request(
        [{"role": "system", "content": "Reply."}, {"role": "user", "content": "hello"}],
        tools=None,
        tool_choice="none",
    )
    assert transport.streamed == 1
    assert transport.created == 0
    assert [event.type for event in events] == [
        "started",
        "text_delta",
        "tool_delta",
        "completed",
    ]

    events.clear()
    non_stream_client = ResponsesClient(
        model="test",
        transport=transport,
        response_mode="non_stream",
        stream_callback=callback,
    )
    non_stream_client.request(
        [{"role": "system", "content": "Reply."}, {"role": "user", "content": "hello"}],
        tools=None,
        tool_choice="none",
    )
    assert transport.created == 1
    assert [event.type for event in events] == ["started", "completed"]


def test_transient_output_does_not_advance_cursor_and_final_history_converges() -> None:
    activation = OwnerActivation(
        activation_id="activation-1",
        triage_id="triage-1",
        task_type=ProjectOwnerTaskType.USER_INPUT,
        message_id="history-1",
    )
    projection = ConversationProjection()
    projection.seed((), (activation,), 0)
    response_id = "response-1"

    projection.apply(
        ConversationResponseUpdated(
            triage_id="triage-1",
            activation_id="activation-1",
            response_id=response_id,
            delta="partial",
        )
    )
    assert projection.cursor == 0
    assert len(projection.visible) == 1
    assert projection.visible[0].content == "partial"

    projection.apply(
        ConversationMessageAppended(
            triage_id="triage-1",
            activation=activation,
            history=MessageHistory(
                project_owner_session_id="session-1",
                message_id="history-1",
                sequence=1,
                message=(
                    {
                        "object": "response",
                        "extra": {"response_id": response_id},
                        "output": [
                            {
                                "type": "message",
                                "content": [{"type": "output_text", "text": "final"}],
                            }
                        ],
                    },
                ),
            ),
        )
    )
    assert projection.cursor == 1
    assert [(item.message_id, item.content) for item in projection.visible] == [
        (f"stream:activation-1:{response_id}", "final")
    ]


def test_partial_stream_failure_notifies_once_without_replaying_output() -> None:
    events: list[ResponseEvent] = []
    client = ResponsesClient(
        model="test",
        transport=_FailingTransport(),
        response_mode="stream",
        stream_callback=lambda _response_id, event: events.append(event),
    )

    try:
        client.request(
            [{"role": "system", "content": "Reply."}, {"role": "user", "content": "hello"}],
            tools=None,
            tool_choice="none",
        )
    except TimeoutError:
        pass
    else:
        raise AssertionError("stream failure should propagate")

    assert [event.type for event in events] == ["started", "text_delta", "failed"]
    assert events[1].delta == "partial"


def test_tool_stream_failure_marks_one_tool_row_without_empty_assistant_row() -> None:
    projection = ConversationProjection()
    projection.seed((), (), 0)
    projection.apply(
        ConversationResponseUpdated(
            triage_id="triage-1",
            activation_id="activation-1",
            response_id="response-1",
            call_id="call-1",
            tool_name="bash",
            delta='{"command":',
        )
    )
    projection.apply(
        ConversationResponseUpdated(
            triage_id="triage-1",
            activation_id="activation-1",
            response_id="response-1",
            failure="upstream timed out",
        )
    )

    assert len(projection.visible) == 1
    assert projection.visible[0].role == "tool"
    assert projection.visible[0].tool_activity is not None
    assert projection.visible[0].tool_activity.status == "failed"
