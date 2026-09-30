"""Incremental projection of persisted Owner messages to the existing UI rows."""

from __future__ import annotations

from dataclasses import replace

from agentplanex.domains.conversation_event import (
    ConversationActivationUpdated,
    ConversationEvent,
    ConversationMessageAppended,
    ConversationResponseUpdated,
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


def _response_id(message: Message) -> str | None:
    extra = message.get("extra")
    if not isinstance(extra, dict):
        return None
    response_id = extra.get("response_id")
    return response_id if isinstance(response_id, str) and response_id else None


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
        self._stream_message_ids: dict[str, str] = {}
        self._stream_tool_ids: dict[tuple[str, str], str] = {}
        self._stream_tool_arguments: dict[tuple[str, str], str] = {}
        self._stream_response_tools: dict[tuple[str, str], set[str]] = {}

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
        self._stream_message_ids.clear()
        self._stream_tool_ids.clear()
        self._stream_tool_arguments.clear()
        self._stream_response_tools.clear()
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
        elif isinstance(event, ConversationResponseUpdated):
            if event.call_id is not None:
                self._apply_stream_tool(event)
                return tuple(
                    item for item in self._visible if before.get(item.message_id) != item
                )
            if event.failure is not None:
                self._apply_stream_failure(event)
                return tuple(
                    item for item in self._visible if before.get(item.message_id) != item
                )
            message_id = f"stream:{event.activation_id}:{event.response_id}"
            self._stream_message_ids[event.activation_id] = message_id
            index = next(
                (
                    index
                    for index, item in enumerate(self._visible)
                    if item.message_id == message_id
                ),
                None,
            )
            if index is None and event.completed and not event.delta:
                return ()
            if index is None:
                self._visible.append(
                    VisibleMessage(
                        message_id,
                        "assistant",
                        event.delta,
                        streaming=event.failure is None and not event.completed,
                        error=event.failure,
                    )
                )
            else:
                current = self._visible[index]
                self._visible[index] = replace(
                    current,
                    content=current.content + event.delta,
                    streaming=event.failure is None and not event.completed,
                    error=event.failure,
                )
        else:
            return ()
        return tuple(item for item in self._visible if before.get(item.message_id) != item)

    def _apply_stream_tool(self, event: ConversationResponseUpdated) -> None:
        assert event.call_id is not None
        key = (event.activation_id, event.call_id)
        self._stream_response_tools.setdefault(
            (event.activation_id, event.response_id), set()
        ).add(event.call_id)
        message_id = self._stream_tool_ids.setdefault(
            key, f"stream:{event.activation_id}:{event.response_id}:tool:{event.call_id}"
        )
        arguments = self._stream_tool_arguments.get(key, "") + event.delta
        self._stream_tool_arguments[key] = arguments
        index = next(
            (index for index, item in enumerate(self._visible) if item.message_id == message_id),
            None,
        )
        activity = ToolActivity(
            name=event.tool_name or "tool",
            status="failed" if event.failure is not None else "running",
            input_preview=tool_preview(arguments),
        )
        row = VisibleMessage(message_id, "tool", event.tool_name or "tool", activity)
        if index is None:
            self._visible.append(row)
        else:
            self._visible[index] = row

    def _apply_stream_failure(self, event: ConversationResponseUpdated) -> None:
        for call_id in self._stream_response_tools.get(
            (event.activation_id, event.response_id), ()
        ):
            message_id = self._stream_tool_ids[(event.activation_id, call_id)]
            index = next(
                (
                    index
                    for index, item in enumerate(self._visible)
                    if item.message_id == message_id
                ),
                None,
            )
            if index is None:
                continue
            current = self._visible[index]
            if current.tool_activity is not None:
                self._visible[index] = replace(
                    current,
                    tool_activity=replace(current.tool_activity, status="failed"),
                )
        message_id = f"stream:{event.activation_id}:{event.response_id}"
        index = next(
            (
                index
                for index, item in enumerate(self._visible)
                if item.message_id == message_id
            ),
            None,
        )
        if index is not None:
            self._visible[index] = replace(
                self._visible[index], streaming=False, error=event.failure
            )

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
        elif activation is not None and activation.status is OwnerActivationStatus.INTERRUPTED:
            self._visible.append(
                VisibleMessage(
                    f"{activation.activation_id}:interrupted",
                    "status",
                    "Owner 已被用户中断",
                )
            )

    def _append_message(self, history: MessageHistory, index: int, message: Message) -> None:
        calls = tool_calls(message)
        for call_id, tool_name, arguments in calls:
            message_id = f"{history.message_id}:{index}:tool:{call_id}"
            if self._current_activation is not None:
                message_id = self._stream_tool_ids.get(
                    (self._current_activation.activation_id, call_id),
                    message_id,
                )
            self._tool_activation_ids[call_id] = (
                self._current_activation.activation_id
                if self._current_activation is not None
                else None
            )
            row = VisibleMessage(
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
            existing_index = next(
                (
                    index
                    for index, item in enumerate(self._visible)
                    if item.message_id == message_id
                ),
                None,
            )
            if existing_index is None:
                self._visible.append(row)
                self._tool_indices[call_id] = len(self._visible) - 1
            else:
                self._visible[existing_index] = row
                self._tool_indices[call_id] = existing_index
        response_text = assistant_response_text(message)
        if response_text:
            self._has_reply = True
            message_id = f"{history.message_id}:{index}"
            if self._current_activation is not None:
                response_id = _response_id(message)
                message_id = (
                    f"stream:{self._current_activation.activation_id}:{response_id}"
                    if response_id is not None
                    else self._stream_message_ids.get(
                        self._current_activation.activation_id,
                        message_id,
                    )
                )
            replacement = VisibleMessage(message_id, "assistant", response_text)
            existing_index = next(
                (
                    index
                    for index, item in enumerate(self._visible)
                    if item.message_id == message_id
                ),
                None,
            )
            if existing_index is None:
                self._visible.append(replacement)
            else:
                self._visible[existing_index] = replacement
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
        elif activation.status is OwnerActivationStatus.INTERRUPTED:
            self._append_interrupted(activation)

    def _append_interrupted(self, activation: OwnerActivation) -> None:
        message_id = f"{activation.activation_id}:interrupted"
        if any(item.message_id == message_id for item in self._visible):
            return
        self._visible.append(VisibleMessage(message_id, "status", "Owner 已被用户中断"))

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
