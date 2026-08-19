"""Project Owner context-window policy and Summary generation."""

import re
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from agentplanex.domains import Message
from agentplanex.project_owner_agent.tools import ToolCatalog


@dataclass(frozen=True, slots=True)
class OwnerContextPolicy:
    """All configurable inputs to the Owner's compaction decision."""

    model_name: str
    capacity_tokens: int
    compaction_threshold: float
    summary_context_header: str
    trajectory_summary_prompt: str
    initial_intent_summary_prompt: str
    update_intent_summary_prompt: str

    def __post_init__(self) -> None:
        for field_name in (
            "model_name",
            "summary_context_header",
            "trajectory_summary_prompt",
            "initial_intent_summary_prompt",
            "update_intent_summary_prompt",
        ):
            if not getattr(self, field_name).strip():
                raise ValueError(f"{field_name} must not be empty")
        if self.capacity_tokens <= 0:
            raise ValueError("capacity_tokens must be positive")
        if not 0 < self.compaction_threshold <= 1:
            raise ValueError("compaction_threshold must be within (0, 1]")


@dataclass(frozen=True, slots=True)
class SummaryDraft:
    """Validated Summary content before Runtime persistence assigns identity."""

    intent_summary_content: str
    trajectory_summary_content: str

    def __post_init__(self) -> None:
        if not self.intent_summary_content.strip():
            raise ValueError("intent_summary_content must not be empty")
        if not self.trajectory_summary_content.strip():
            raise ValueError("trajectory_summary_content must not be empty")


class ContextCompactionPhase(StrEnum):
    STARTED = "STARTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class ContextCompactionNotice:
    """Agent-owned compaction fact for Runtime Timeline persistence."""

    phase: ContextCompactionPhase
    compaction_id: str
    query_index: int
    covered_through_message_id: str
    estimated_tokens: int
    capacity_tokens: int
    compaction_threshold: float
    summary_id: str | None = None
    failure_type: str | None = None

    def __post_init__(self) -> None:
        if not self.compaction_id.strip():
            raise ValueError("compaction_id must not be empty")
        if self.query_index < 0:
            raise ValueError("query_index must not be negative")
        if not self.covered_through_message_id.strip():
            raise ValueError("covered_through_message_id must not be empty")
        if self.estimated_tokens < 0:
            raise ValueError("estimated_tokens must not be negative")
        if self.capacity_tokens <= 0:
            raise ValueError("capacity_tokens must be positive")
        if not 0 < self.compaction_threshold <= 1:
            raise ValueError("compaction_threshold must be within (0, 1]")
        if (self.phase is ContextCompactionPhase.COMPLETED) != (
            self.summary_id is not None
        ):
            raise ValueError("Only a completed compaction has a summary_id")
        if (self.phase is ContextCompactionPhase.FAILED) != (
            self.failure_type is not None
        ):
            raise ValueError("Only a failed compaction has a failure_type")


class SummaryModel(Protocol):
    def text(
        self,
        messages: list[Message],
        *,
        tools: ToolCatalog,
    ) -> str: ...


def generate_summary(
    messages: Sequence[Message],
    *,
    has_source_summary: bool,
    policy: OwnerContextPolicy,
    tools: ToolCatalog,
    model: SummaryModel,
) -> SummaryDraft:
    """Generate and validate intent and trajectory summaries in parallel."""

    frozen = tuple(dict(message) for message in messages)
    intent_prompt = (
        policy.update_intent_summary_prompt
        if has_source_summary
        else policy.initial_intent_summary_prompt
    )

    def summarize(prompt: str) -> str:
        request = [
            *(dict(message) for message in frozen),
            {"role": "developer", "content": prompt.strip()},
        ]
        return model.text(request, tools=tools)

    with ThreadPoolExecutor(max_workers=2) as executor:
        trajectory_future = executor.submit(
            summarize,
            policy.trajectory_summary_prompt,
        )
        intent_future = executor.submit(summarize, intent_prompt)
        trajectory_content = _extract_summary(
            trajectory_future.result(),
            "trajectory-summary",
        )
        intent_content = _extract_summary(
            intent_future.result(),
            "intent-summary",
        )
    return SummaryDraft(
        intent_summary_content=intent_content,
        trajectory_summary_content=trajectory_content,
    )


def count_tokens(
    model_name: str,
    messages: Sequence[Message],
    tools: ToolCatalog,
) -> int:
    """Count the complete model request, including tool schemas."""

    from litellm import token_counter

    return int(
        token_counter(
            model=model_name,
            messages=[_token_counter_message(message) for message in messages],
            tools=tools.provider_schemas(),
            tool_choice="auto",
        )
    )


def _extract_summary(response: str, tag: str) -> str:
    pattern = re.compile(
        rf"<{re.escape(tag)}>(?P<content>.*)</{re.escape(tag)}>",
        re.DOTALL,
    )
    match = pattern.fullmatch(response.strip())
    if match is None:
        raise ValueError(f"Summary response must contain exactly one <{tag}> root")
    content = match.group("content").strip()
    if not content:
        raise ValueError(f"Summary response <{tag}> must not be empty")
    if f"<{tag}>" in content or f"</{tag}>" in content:
        raise ValueError(f"Summary response must contain exactly one <{tag}> root")
    return content


def _token_counter_message(message: Message) -> Message:
    """Translate Responses input_text parts to LiteLLM's chat counter shape."""

    normalized = dict(message)
    content = normalized.get("content")
    if isinstance(content, list):
        normalized["content"] = [
            (
                {**part, "type": "text"}
                if isinstance(part, dict) and part.get("type") == "input_text"
                else part
            )
            for part in content
        ]
    return normalized
