"""Responses model contract and Project Owner response handling."""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, NoReturn, Protocol, cast

from agentplanex.project_owner_agent.contracts import (
    Action,
    ActionOutput,
    Message,
    ToolSchema,
)
from agentplanex.project_owner_agent.exception import (
    FormatError,
    ModelError,
    ReplyToHuman,
)
from agentplanex.project_owner_agent.tools.base import ToolCatalog

type ToolChoice = Literal["auto", "none"]


@dataclass(frozen=True, slots=True)
class ResponsesRequest:
    """One provider-neutral Responses request at the external transport seam."""

    model: str
    instructions: str
    input: tuple[Message, ...]
    tools: tuple[ToolSchema, ...]
    tool_choice: ToolChoice


class ResponsesTransport(Protocol):
    def create(self, request: ResponsesRequest) -> object: ...


@dataclass(frozen=True, slots=True)
class ResponsesClient:
    """Serialize one shared Responses request and expose its raw output."""

    model: str
    transport: ResponsesTransport

    def request(
        self,
        messages: list[Message],
        *,
        tools: ToolCatalog | None,
        tool_choice: ToolChoice,
    ) -> tuple[Message, list[object]]:
        instructions, response_input = _prepare_input(messages)
        request = ResponsesRequest(
            model=self.model,
            instructions=instructions,
            input=tuple(response_input),
            tools=tuple(tools.provider_schemas()) if tools is not None else (),
            tool_choice=tool_choice,
        )
        response = self.transport.create(request)
        message = _serialize(response)
        return message, _as_list(_get(response, "output"))

    def text(
        self,
        messages: list[Message],
        *,
        tools: ToolCatalog,
    ) -> str:
        message, output = self.request(
            messages,
            tools=tools,
            tool_choice="none",
        )
        if any(_get(item, "type") == "function_call" for item in output):
            raise ModelError("Summary response attempted a tool call")
        reply = _extract_reply(output)
        if not reply:
            raise ModelError(f"Summary response has no text: {message!r}")
        return reply


class ProjectOwnerModel:
    def __init__(
        self,
        *,
        tools: ToolCatalog | None,
        responses: ResponsesClient,
    ) -> None:
        self.tools = tools
        self.responses = responses

    def query(self, messages: list[Message]) -> Message:
        message, output = self.responses.request(
            messages,
            tools=self.tools,
            tool_choice="auto" if self.tools is not None else "none",
        )
        if self.tools is None and any(
            _get(item, "type") == "function_call" for item in output
        ):
            _raise_format_error(
                "This model invocation does not allow tool calls.",
                message,
            )
        actions = (
            _parse_actions(output, message, self.tools)
            if self.tools is not None
            else []
        )
        if actions:
            message["extra"] = {"actions": actions}
            return message

        reply = _extract_reply(output)
        if reply:
            raise ReplyToHuman(content=reply, response=message)

        _raise_format_error(
            "Response must contain a tool call or a non-empty natural-language reply.",
            message,
        )

    def format_observation_messages(
        self,
        message: Message,
        outputs: list[ActionOutput],
    ) -> list[Message]:
        extra = message.get("extra")
        actions = extra.get("actions", []) if isinstance(extra, dict) else []
        if len(actions) != len(outputs):
            raise ModelError("every tool action must have exactly one observation")
        return [
            format_tool_output_message(action, output)
            for action, output in zip(actions, outputs, strict=True)
        ]


def format_tool_call_message(action: Action) -> Message:
    """Encode one explicit Action as a Responses API function-call input."""

    tool, call_id, arguments = _action_parts(action)
    return {
        "type": "function_call",
        "call_id": call_id,
        "name": tool,
        "arguments": json.dumps(
            arguments,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    }


def format_tool_output_message(
    action: Action,
    output: ActionOutput,
) -> Message:
    """Encode one Tool observation for persisted Responses API history."""

    _, call_id, _ = _action_parts(action)
    return {
        "type": "function_call_output",
        "call_id": call_id,
        "output": _render_output(output),
    }


def _prepare_input(messages: list[Message]) -> tuple[str, list[Message]]:
    instructions = ""
    result: list[Message] = []
    for message in messages:
        if message.get("role") == "system" and not instructions:
            instructions = str(message.get("content", ""))
        elif message.get("object") == "response":
            result.extend(
                _without_extra(_serialize(item))
                for item in _as_list(message.get("output"))
            )
        else:
            result.append(_without_extra(message))
    if not instructions:
        raise ModelError("message history has no system instructions")
    return instructions, result


def _parse_actions(
    output: list[object],
    response: Message,
    tools: ToolCatalog,
) -> list[Action]:
    actions: list[Action] = []
    for item in output:
        if _get(item, "type") != "function_call":
            continue
        name = _get(item, "name")
        if not isinstance(name, str) or not name:
            _raise_format_error("Tool call requires a non-empty name.", response)

        raw_arguments = _get(item, "arguments")
        try:
            arguments = (
                json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
            )
        except json.JSONDecodeError as error:
            _raise_format_error(f"Invalid tool arguments: {error}.", response)

        if not isinstance(arguments, dict):
            _raise_format_error("Tool arguments must be a JSON object.", response)
        call_id = _get(item, "call_id") or _get(item, "id")
        if not isinstance(call_id, str) or not call_id:
            _raise_format_error("Tool call requires a call_id.", response)
        try:
            action = tools.create_action(
                name=name,
                call_id=call_id,
                arguments=arguments,
            )
        except ValueError as error:
            _raise_format_error(f"{error}.", response)
        actions.append(action)
    return actions


def _extract_reply(output: list[object]) -> str:
    text_parts: list[str] = []
    for item in output:
        if _get(item, "type") != "message":
            continue
        for content in _as_list(_get(item, "content")):
            if _get(content, "type") in {"output_text", "text"}:
                text = _get(content, "text")
                if isinstance(text, str) and text:
                    text_parts.append(text)
    return "\n".join(text_parts).strip()


def _raise_format_error(error: str, response: Message) -> NoReturn:
    raise FormatError(content=error, response=response)


def _render_output(output: ActionOutput) -> str:
    return json.dumps(output, ensure_ascii=False)


def _action_parts(action: Action) -> tuple[str, str, dict[str, Any]]:
    tool = action.get("tool")
    call_id = action.get("call_id")
    arguments = action.get("arguments")
    if not isinstance(tool, str) or not tool.strip():
        raise ValueError("Tool action must contain a non-empty string 'tool'")
    if not isinstance(call_id, str) or not call_id.strip():
        raise ValueError("Tool action must contain a non-empty string 'call_id'")
    if not isinstance(arguments, dict):
        raise ValueError("Tool action must contain an object 'arguments'")
    return tool, call_id, arguments


def _without_extra(message: Message) -> Message:
    return {key: value for key, value in message.items() if key != "extra"}


def _serialize(value: object) -> Message:
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json")
        if isinstance(dumped, dict):
            return dict(dumped)
    try:
        return dict(cast(Mapping[str, Any], value))
    except Exception as error:
        raise ModelError(
            f"Responses gateway returned an unserializable response: {value!r}"
        ) from error


def _get(value: object, key: str) -> Any:
    return value.get(key) if isinstance(value, dict) else getattr(value, key, None)


def _as_list(value: object) -> list[object]:
    return list(value) if isinstance(value, (list, tuple)) else []
