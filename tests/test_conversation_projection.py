"""The stream projection keeps the established conversation row semantics."""

from datetime import UTC, datetime

from agentplanex.domains.conversation_event import (
    ConversationActivationUpdated,
    ConversationMessageAppended,
)
from agentplanex.project_owner_agent.context.models import MessageHistory
from agentplanex.services.project_runtime_context.models import (
    OwnerActivation,
    OwnerActivationMode,
    OwnerActivationStatus,
    ProjectOwnerTaskType,
)
from agentplanex.services.web.conversation_projection import ConversationProjection


def _activation(status: OwnerActivationStatus) -> OwnerActivation:
    now = datetime.now(UTC)
    return OwnerActivation(
        activation_id="activation-1",
        triage_id="feature-a",
        task_type=ProjectOwnerTaskType.USER_INPUT,
        message_id="input-1",
        status=status,
        driver_mode=OwnerActivationMode.MODEL,
        started_at=now,
        finished_at=now if status is not OwnerActivationStatus.RUNNING else None,
        failure=None,
    )


def test_projection_updates_one_tool_row_without_rebuilding_history() -> None:
    input_history = MessageHistory(
        project_owner_session_id="session-1",
        message_id="input-1",
        sequence=1,
        message=({"role": "user", "content": "run it"},),
    )
    tool_history = MessageHistory(
        project_owner_session_id="session-1",
        message_id="tool-1",
        sequence=2,
        message=(
            {
                "type": "function_call",
                "call_id": "call-1",
                "name": "bash",
                "arguments": "{}",
            },
        ),
    )
    output_history = MessageHistory(
        project_owner_session_id="session-1",
        message_id="output-1",
        sequence=3,
        message=(
            {
                "type": "function_call_output",
                "call_id": "call-1",
                "output": '{"ok": true}',
            },
        ),
    )

    projection = ConversationProjection()
    projection.seed(
        (input_history, tool_history),
        (_activation(OwnerActivationStatus.RUNNING),),
        sequence=2,
    )
    assert [item.role for item in projection.visible] == ["user", "tool"]
    assert projection.visible[-1].tool_activity is not None
    assert projection.visible[-1].tool_activity.status == "running"

    changed = projection.apply(ConversationMessageAppended("feature-a", output_history))

    assert len(changed) == 1
    assert changed[0].message_id == projection.visible[-1].message_id
    assert projection.visible[-1].tool_activity is not None
    assert projection.visible[-1].tool_activity.status == "completed"

    finished = _activation(OwnerActivationStatus.COMPLETED)
    assert projection.apply(ConversationActivationUpdated("feature-a", finished)) == ()


def test_reply_and_tool_state_follow_activation_updates() -> None:
    from dataclasses import replace

    pending = OwnerActivation(
        activation_id="activation-1",
        triage_id="feature-a",
        task_type=ProjectOwnerTaskType.USER_INPUT,
        message_id="input-1",
    )
    projection = ConversationProjection()
    projection.seed((), (), 0)
    projection.apply(
        ConversationMessageAppended(
            "feature-a",
            MessageHistory(
                project_owner_session_id="session-1",
                message_id="input-1",
                sequence=1,
                message=({"role": "user", "content": "Run a tool"},),
            ),
            pending,
        )
    )
    running = replace(
        pending,
        status=OwnerActivationStatus.RUNNING,
        driver_mode=OwnerActivationMode.MODEL,
        started_at=datetime.now(UTC),
    )
    projection.apply(ConversationActivationUpdated("feature-a", running))
    rows = projection.apply(
        ConversationMessageAppended(
            "feature-a",
            MessageHistory(
                project_owner_session_id="session-1",
                message_id="tool-1",
                sequence=2,
                message=(
                    {
                        "type": "function_call",
                        "call_id": "call-1",
                        "name": "bash",
                        "arguments": "{}",
                    },
                ),
            ),
        )
    )
    assert rows[0].tool_activity is not None
    assert rows[0].tool_activity.status == "running"
    assert not projection.activation_has_reply

    projection.apply(
        ConversationMessageAppended(
            "feature-a",
            MessageHistory(
                project_owner_session_id="session-1",
                message_id="reply-1",
                sequence=3,
                message=({"role": "assistant", "content": "Working on it"},),
            ),
        )
    )
    assert projection.activation_has_reply
    failed = replace(
        running,
        status=OwnerActivationStatus.FAILED,
        finished_at=datetime.now(UTC),
        failure="Tool interrupted",
    )
    rows = projection.apply(ConversationActivationUpdated("feature-a", failed))
    assert rows[0].tool_activity is not None
    assert rows[0].tool_activity.status == "failed"
    assert rows[1].content == "Project Owner failed: Tool interrupted"
    assert not projection.activation_has_reply
    assert projection.apply(ConversationActivationUpdated("feature-a", failed)) == ()
