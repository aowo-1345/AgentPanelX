"""OpenAI Responses adapter for the JBB gateway."""

import json
from collections.abc import Mapping
from typing import Any, NoReturn, cast

from openai import OpenAI
from openai.types.responses import FunctionToolParam, ResponseInputParam

from agentplanex.domains import Action, ActionOutput
from agentplanex.project_owner_agent.exception import (
    FormatError,
    JBBModelError,
    ReplyToHuman,
)
from agentplanex.project_owner_agent.models.base import Message
from agentplanex.project_owner_agent.tools.base import ToolCatalog

DEFAULT_JBB_BASE_URL = "https://api.openai.com/v1"


class JBBModel:
    def __init__(
        self,
        *,
        model: str,
        tools: ToolCatalog,
        base_url: str = DEFAULT_JBB_BASE_URL,
        timeout_seconds: float = 60.0,
    ) -> None:
        if not model.strip():
            raise ValueError("model must not be empty")
        if not base_url.strip():
            raise ValueError("base_url must not be empty")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.model = model
        self.base_url = base_url
        self.tools = tools
        self.timeout_seconds = timeout_seconds
        try:
            self.client = OpenAI(
                base_url=base_url,
                timeout=timeout_seconds,
            )
        except Exception as error:
            raise JBBModelError(f"Failed to initialize JBB gateway: {error}") from error

    def query(self, messages: list[Message]) -> Message:
        instructions, response_input = _prepare_input(messages)
        try:
            response = self.client.responses.create(
                model=self.model,
                instructions=instructions,
                input=cast(ResponseInputParam, response_input),
                tools=cast(list[FunctionToolParam], self.tools.provider_schemas()),
                store=False,
                stream=False,
                service_tier="priority",
                tool_choice="auto",
                parallel_tool_calls=True,
            )
        except Exception as error:
            raise JBBModelError(f"JBB gateway request failed: {error}") from error

        message = _serialize(response)
        output = _as_list(_get(response, "output"))
        actions = _parse_actions(output, message, self.tools)
        if actions:
            message["extra"] = {"actions": actions}
            return message

        reply = _extract_reply(output)
        if reply:
            message["role"] = "exit"
            message["content"] = reply
            message["extra"] = {
                "exit_status": "ReplyToHuman",
                "submission": reply,
            }
            raise ReplyToHuman(message)

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
            raise JBBModelError("every tool action must have exactly one observation")
        return [
            {
                "type": "function_call_output",
                "call_id": action["call_id"],
                "output": _render_output(output),
            }
            for action, output in zip(actions, outputs, strict=True)
        ]


def _prepare_input(messages: list[Message]) -> tuple[str, list[Message]]:
    instructions = ""
    result: list[Message] = []
    for message in messages:
        if message.get("role") == "system":
            instructions = str(message.get("content", ""))
        elif message.get("object") == "response":
            result.extend(
                _without_extra(_serialize(item))
                for item in _as_list(message.get("output"))
            )
        else:
            result.append(_without_extra(message))
    if not instructions:
        raise JBBModelError("message history has no system instructions")
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
    raise FormatError(
        {
            "role": "user",
            "content": error,
            "extra": {
                "interrupt_type": "FormatError",
                "response": response,
            },
        }
    )


def _render_output(output: ActionOutput) -> str:
    return json.dumps(output, ensure_ascii=False)


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
        raise JBBModelError(f"JBB returned an unserializable response: {value!r}") from error


def _get(value: object, key: str) -> Any:
    return value.get(key) if isinstance(value, dict) else getattr(value, key, None)


def _as_list(value: object) -> list[object]:
    return list(value) if isinstance(value, (list, tuple)) else []
