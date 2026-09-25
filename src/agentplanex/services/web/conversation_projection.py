"""Incremental projection of persisted Owner messages to the existing UI rows."""

from __future__ import annotations

from dataclasses import replace

from agentplanex.domains.conversation_event import (
    ConversationActivationUpdated,
    ConversationEvent,
    ConversationMessageAppended,
)
from agentplanex.project_owner_agent.context.models import MessageHistory
from agentplanex.project_owner_agent.contracts import Message
from agentplanex.services.project_runtime_context.models import (
    OwnerActivation,
    OwnerActivationStatus,
    ProjectOwnerTaskType,
)
from agentplanex.services.web.project_workspace import (
    ToolActivity,
    ToolActivityStatus,
    VisibleMessage,
    assistant_response_text,
    decoded_tool_output,
    plan_decision_text,
    tool_calls,
    tool_output_failed,
    tool_preview,
)


class ConversationProjection:
    """Apply one committed event at a time without rebuilding message history."""

    def __init__(self) -> None:
        self._activations: dict[str, OwnerActivation] = {}
        self._visible: list[VisibleMessage] = []
        self._tool_indices: dict[str, int] = {}
        self._tool_activation_ids: dict[str, str | None] = {}
        self._failure_rows: set[str] = set()
        self._has_reply = False
        self.cursor = 0
        self._current_activation: OwnerActivation | None = None

    @property
    def visible(self) -> tuple[VisibleMessage, ...]:
        return tuple(self._visible)

    @property
    def activation_has_reply(self) -> bool:
        return (
            self._has_reply
            and self._current_activation is not None
            and self._current_activation.status in (
                OwnerActivationStatus.PENDING, OwnerActivationStatus.RUNNING
            )
        )

    def seed(
        self,
        histories: tuple[MessageHistory, ...],
        activations: tuple[OwnerActivation, ...],
        sequence: int,
    ) -> None:
        self._activations = {item.activation_id: item for item in activations}
        self._visible.clear()
        self._tool_indices.clear()
        self._tool_activation_ids.clear()
        self._failure_rows.clear()
        self._current_activation = None
        self._has_reply = False
        for history in histories:
            self._append_history(history)
        self.cursor = sequence

    def apply(self, event: ConversationEvent) -> tuple[VisibleMessage, ...]:
        before = {item.message_id: item for item in self._visible}
        if isinstance(event, ConversationMessageAppended):
            if event.history.sequence <= self.cursor:
                return ()
            if event.activation is not None:
                self._activations[event.activation.activation_id] = event.activation
            self._append_history(event.history)
            self.cursor = event.history.sequence
        elif isinstance(event, ConversationActivationUpdated):
            self._activations[event.activation.activation_id] = event.activation
            self._update_activation(event.activation)
        else:
            return ()
        return tuple(item for item in self._visible if before.get(item.message_id) != item)

    def _append_history(self, history: MessageHistory) -> None:
        activation = next(
            (
                item
                for item in self._activations.values()
                if item.message_id == history.message_id
            ),
            None,
        )
        if activation is not None:
            self._current_activation = activation
            self._has_reply = False
        for index, message in enumerate(history.message):
            self._append_message(history, index, message)
        if activation is not None and activation.failure is not None:
            self._append_failure(activation)

    def _append_message(self, history: MessageHistory, index: int, message: Message) -> None:
        calls = tool_calls(message)
        for call_id, tool_name, arguments in calls:
            message_id = f"{history.message_id}:{index}:tool:{call_id}"
            self._tool_indices[call_id] = len(self._visible)
            self._tool_activation_ids[call_id] = (
                self._current_activation.activation_id
                if self._current_activation is not None
                else None
            )
            self._visible.append(
                VisibleMessage(
                    message_id,
                    "tool",
                    tool_name,
                    ToolActivity(
                        name=tool_name,
                        status=(
                            "running"
                            if self._current_activation is not None
                            and self._current_activation.status is OwnerActivationStatus.RUNNING
                            else "failed"
                        ),
                        input_preview=tool_preview(arguments),
                    ),
                )
            )
        response_text = assistant_response_text(message)
        if response_text:
            self._has_reply = True
            self._visible.append(
                VisibleMessage(f"{history.message_id}:{index}", "assistant", response_text)
            )
            return
        if calls:
            return
        if message.get("type") == "function_call_output":
            self._append_tool_output(history, index, message)
            return
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            return
        if message.get("role") == "assistant":
            self._has_reply = True
            self._visible.append(
                VisibleMessage(f"{history.message_id}:{index}", "assistant", content)
            )
        elif message.get("role") == "user" and self._current_activation is not None:
            if self._current_activation.task_type is ProjectOwnerTaskType.USER_INPUT:
                self._visible.append(
                    VisibleMessage(f"{history.message_id}:{index}", "user", content)
                )
            elif self._current_activation.task_type is ProjectOwnerTaskType.PLAN_DECISION:
                self._visible.append(
                    VisibleMessage(
                        f"{history.message_id}:{index}",
                        "status",
                        plan_decision_text(content),
                    )
                )

    def _append_tool_output(self, history: MessageHistory, index: int, message: Message) -> None:
        call_id = message.get("call_id")
        output = decoded_tool_output(message.get("output"))
        output_preview = tool_preview(output)
        status: ToolActivityStatus = "failed" if tool_output_failed(output) else "completed"
        tool_index = self._tool_indices.get(call_id) if isinstance(call_id, str) else None
        if tool_index is None:
            self._visible.append(
                VisibleMessage(
                    f"{history.message_id}:{index}:tool:{call_id}",
                    "tool",
                    "tool",
                    ToolActivity(
                        name="tool",
                        status=status,
                        input_preview="{}",
                        output_preview=output_preview,
                    ),
                )
            )
            return
        current = self._visible[tool_index]
        activity = current.tool_activity
        if activity is not None:
            self._visible[tool_index] = replace(
                current,
                tool_activity=ToolActivity(
                    name=activity.name,
                    status=status,
                    input_preview=activity.input_preview,
                    output_preview=output_preview,
                ),
            )

    def _update_activation(self, activation: OwnerActivation) -> None:
        if (
            self._current_activation is not None
            and self._current_activation.activation_id == activation.activation_id
        ):
            self._current_activation = activation
        for call_id, activation_id in self._tool_activation_ids.items():
            if activation_id != activation.activation_id:
                continue
            index = self._tool_indices[call_id]
            current = self._visible[index]
            activity = current.tool_activity
            if activity is None or activity.output_preview is not None:
                continue
            status: ToolActivityStatus = (
                "running" if activation.status is OwnerActivationStatus.RUNNING else "failed"
            )
            if activity.status != status:
                self._visible[index] = replace(
                    current,
                    tool_activity=replace(activity, status=status),
                )
        if activation.failure is not None:
            self._append_failure(activation)

    def _append_failure(self, activation: OwnerActivation) -> None:
        if activation.activation_id in self._failure_rows:
            return
        self._failure_rows.add(activation.activation_id)
        self._visible.append(
            VisibleMessage(
                f"{activation.activation_id}:failure",
                "status",
                f"Project Owner failed: {activation.failure}",
            )
        )
