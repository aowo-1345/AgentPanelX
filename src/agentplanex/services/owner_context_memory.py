"""Query-time context compaction for one persistent Project Owner."""

import re
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from uuid import uuid4

from agentplanex.domains import (
    ExecutionEvent,
    ExecutionEventType,
    Message,
    OwnerActivation,
    ProjectRuntimeContext,
    SummaryHistory,
)
from agentplanex.infrastructure.sqlite import SQLiteDatabase
from agentplanex.infrastructure.sqlite.repositories import (
    SQLiteOwnerActivationRepository,
    SQLiteProjectOwnerAgentRepository,
    SQLiteSummaryHistoryRepository,
)
from agentplanex.project_owner_agent.models.responses import ResponsesClient
from agentplanex.project_owner_agent.tools import ToolCatalog
from agentplanex.services.event_bus import EventBus
from agentplanex.services.owner_context import render_summary_messages
from agentplanex.settings import Settings


@dataclass(slots=True)
class ProjectOwnerContextMemory:
    """Hide Owner token checks, dual compaction and atomic publication."""

    database: SQLiteDatabase
    settings: Settings
    tools: ToolCatalog
    responses: ResponsesClient
    event_bus: EventBus
    owners: SQLiteProjectOwnerAgentRepository = field(
        default_factory=SQLiteProjectOwnerAgentRepository
    )
    activations: SQLiteOwnerActivationRepository = field(
        default_factory=SQLiteOwnerActivationRepository
    )
    summaries: SQLiteSummaryHistoryRepository = field(
        default_factory=SQLiteSummaryHistoryRepository
    )

    def prepare_query(
        self,
        context: ProjectRuntimeContext,
        activation: OwnerActivation,
        query_index: int,
        messages: Sequence[Message],
    ) -> tuple[Message, ...]:
        """Return the original request or one newly published Summary projection."""

        frozen = tuple(dict(message) for message in messages)
        if not frozen or frozen[0].get("role") != "system":
            raise RuntimeError("Owner context must start with a System Prompt")

        with self.database.read_only_connection() as connection:
            owner = self.owners.get_by_triage_id(connection, context.triage_id)
        if owner is None or owner.message_id is None:
            raise RuntimeError("Project Owner has no persisted message checkpoint")
        fixed_tools = self.tools.select(owner.tools)
        memory = self.settings.project_owner_agent.context_memory
        estimate = _count_tokens(
            self.settings.project_owner_agent.selected_model.name,
            frozen,
            fixed_tools,
        )
        if estimate / memory.capacity_tokens < memory.compaction_threshold:
            return frozen

        compaction_id = uuid4().hex
        common_payload = {
            "compaction_id": compaction_id,
            "activation_id": activation.activation_id,
            "query_index": query_index,
            "covered_through_message_id": owner.message_id,
            "estimated_tokens": estimate,
            "capacity_tokens": memory.capacity_tokens,
            "compaction_threshold": memory.compaction_threshold,
        }
        self._publish_event(
            context.triage_id,
            ExecutionEventType.CONTEXT_COMPACTION_STARTED,
            common_payload,
            activation.activation_id,
        )
        try:
            summary = self._generate_and_publish(
                activation=activation,
                query_index=query_index,
                owner_session_id=owner.project_owner_session_id,
                source_summary_id=owner.summary_id,
                watermark=owner.message_id,
                frozen=frozen,
                fixed_tools=fixed_tools,
            )
        except Exception as error:
            self._publish_event(
                context.triage_id,
                ExecutionEventType.CONTEXT_COMPACTION_FAILED,
                {**common_payload, "failure_type": type(error).__name__},
                activation.activation_id,
            )
            return frozen

        self._publish_event(
            context.triage_id,
            ExecutionEventType.CONTEXT_COMPACTION_COMPLETED,
            {**common_payload, "summary_id": summary.summary_id},
            activation.activation_id,
        )
        return (
            dict(frozen[0]),
            *render_summary_messages(
                summary,
                self.settings.runtime.prompts.summary_context_header,
            ),
        )

    def _generate_and_publish(
        self,
        *,
        activation: OwnerActivation,
        query_index: int,
        owner_session_id: str,
        source_summary_id: str | None,
        watermark: str,
        frozen: tuple[Message, ...],
        fixed_tools: ToolCatalog,
    ) -> SummaryHistory:
        prompts = self.settings.runtime.prompts
        intent_prompt = (
            prompts.initial_intent_summary
            if source_summary_id is None
            else prompts.update_intent_summary
        )

        def summarize(prompt: str) -> str:
            request = [
                *(dict(message) for message in frozen),
                {"role": "developer", "content": prompt.strip()},
            ]
            return self.responses.text(request, tools=fixed_tools)

        with ThreadPoolExecutor(max_workers=2) as executor:
            trajectory_future = executor.submit(
                summarize,
                prompts.trajectory_summary,
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

        summary = SummaryHistory(
            project_owner_session_id=owner_session_id,
            summary_id=uuid4().hex,
            covered_through_message_id=watermark,
            intent_summary_content=intent_content,
            trajectory_summary_content=trajectory_content,
        )
        with self.database.transaction() as connection:
            self.summaries.insert(connection, summary)
            self.owners.advance_summary(
                connection,
                session_id=owner_session_id,
                expected_message_id=watermark,
                expected_summary_id=source_summary_id,
                summary_id=summary.summary_id,
            )
            if query_index == 0:
                self.activations.set_initial_summary(
                    connection,
                    activation.activation_id,
                    summary.summary_id,
                )
        return summary

    def _publish_event(
        self,
        triage_id: str,
        event_type: ExecutionEventType,
        payload: dict[str, object],
        react_loop_id: str,
    ) -> None:
        self.event_bus.publish(
            ExecutionEvent(
                triage_id=triage_id,
                event_type=event_type,
                react_loop_id=react_loop_id,
                payload=payload,
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


def _count_tokens(
    model: str,
    messages: Sequence[Message],
    tools: ToolCatalog,
) -> int:
    from litellm import token_counter

    return int(
        token_counter(
            model=model,
            messages=[_token_counter_message(message) for message in messages],
            tools=tools.provider_schemas(),
            tool_choice="auto",
        )
    )
