"""The Project Owner Agent's model-context interface."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4

from agentplanex.domains.project_runtime_state import ProjectRuntimeState
from agentplanex.project_owner_agent.context.compaction import (
    ContextCompactionAttempt,
    ContextCompactionNotice,
    ContextCompactionPhase,
    OwnerContextPolicy,
    SummaryDraft,
    SummaryModel,
    count_tokens,
    generate_summary,
)
from agentplanex.project_owner_agent.context.models import MessageHistory, SummaryHistory
from agentplanex.project_owner_agent.context.rendering import render_owner_context, render_summary
from agentplanex.project_owner_agent.contracts import Message
from agentplanex.project_owner_agent.tools import ToolCatalog
from agentplanex.services.project_runtime_context.models import OwnerActivation


@dataclass(frozen=True, slots=True)
class OwnerContextSnapshot:
    """Raw persisted facts selected through one Owner message checkpoint."""

    triage_id: str
    project_owner_session_id: str
    through_message_id: str
    through_sequence: int
    system_prompt: str
    tools: tuple[str, ...]
    summary: SummaryHistory | None
    covered_through_sequence: int | None
    message_history: tuple[MessageHistory, ...]

    def __post_init__(self) -> None:
        for field_name in (
            "triage_id",
            "project_owner_session_id",
            "through_message_id",
            "system_prompt",
        ):
            if not getattr(self, field_name).strip():
                raise ValueError(f"{field_name} must not be empty")
        if self.through_sequence <= 0:
            raise ValueError("through_sequence must be positive")
        if (self.summary is None) != (self.covered_through_sequence is None):
            raise ValueError("Summary and covered sequence must be present together")
        if self.summary is not None:
            if self.summary.project_owner_session_id != self.project_owner_session_id:
                raise ValueError("Summary does not belong to Owner session")
            assert self.covered_through_sequence is not None
            if self.covered_through_sequence <= 0:
                raise ValueError("covered_through_sequence must be positive")
            if self.covered_through_sequence > self.through_sequence:
                raise ValueError("Summary watermark must not follow through_sequence")
        for history in self.message_history:
            if history.project_owner_session_id != self.project_owner_session_id:
                raise ValueError("Message history does not belong to Owner session")
            if history.sequence > self.through_sequence:
                raise ValueError("Message history must not follow through_sequence")
            if (
                self.covered_through_sequence is not None
                and history.sequence <= self.covered_through_sequence
            ):
                raise ValueError("Message history must follow Summary watermark")


@dataclass(frozen=True, slots=True)
class LoadedOwnerContext:
    """A bounded checkpoint projection plus an opaque live Runtime revision."""

    snapshot: OwnerContextSnapshot
    revision: object


@dataclass(frozen=True, slots=True)
class CommittedOwnerSummary:
    """A persisted Summary and the opaque revision created by its commit."""

    summary: SummaryHistory
    revision: object


class OwnerContextRuntime(Protocol):
    """Runtime effects required by one live Owner context."""

    def load_context(
        self,
        context: ProjectRuntimeState,
        activation: OwnerActivation,
    ) -> LoadedOwnerContext: ...

    def append_messages(
        self,
        context: ProjectRuntimeState,
        appended: tuple[Message, ...],
        *,
        expected_revision: object,
    ) -> object: ...

    def commit_summary(
        self,
        context: ProjectRuntimeState,
        activation: OwnerActivation,
        *,
        expected_revision: object,
        query_index: int,
        draft: SummaryDraft,
    ) -> CommittedOwnerSummary: ...

    def record_compaction(
        self,
        context: ProjectRuntimeState,
        activation: OwnerActivation,
        notice: ContextCompactionNotice,
        *,
        revision: object,
    ) -> None: ...


class OwnerContextManager:
    """Own one live Owner's model-visible context and persisted revision."""

    def __init__(
        self,
        *,
        runtime: OwnerContextRuntime,
        runtime_context: ProjectRuntimeState,
        activation: OwnerActivation,
        messages: Sequence[Message],
        revision: object,
        has_source_summary: bool,
        policy: OwnerContextPolicy,
        tools: ToolCatalog,
        summary_model: SummaryModel,
    ) -> None:
        self._runtime = runtime
        self._runtime_context = runtime_context
        self._activation = activation
        self._messages = [dict(message) for message in messages]
        self._revision = revision
        self._policy = policy
        self._tools = tools
        self._summary_model = summary_model
        self._has_source_summary = has_source_summary

    @classmethod
    def restore(
        cls,
        *,
        runtime: OwnerContextRuntime,
        runtime_context: ProjectRuntimeState,
        activation: OwnerActivation,
        invocation_text: str,
        policy: OwnerContextPolicy,
        tools: ToolCatalog,
        summary_model: SummaryModel,
    ) -> "OwnerContextManager":
        """Load raw checkpoint facts and render the initial model view."""

        loaded = runtime.load_context(runtime_context, activation)
        snapshot = loaded.snapshot
        if snapshot.triage_id != runtime_context.triage_id:
            raise RuntimeError("Restored Owner context does not match Runtime context")
        if snapshot.through_message_id != activation.message_id:
            raise RuntimeError("Restored Owner context does not match Activation message")
        selected_summary_id = (
            snapshot.summary.summary_id if snapshot.summary is not None else None
        )
        if selected_summary_id != activation.summary_id:
            raise RuntimeError("Restored Owner context does not match Activation Summary")
        return cls(
            runtime=runtime,
            runtime_context=runtime_context,
            activation=activation,
            messages=render_owner_context(
                system_prompt=snapshot.system_prompt,
                summary=snapshot.summary,
                message_history=snapshot.message_history,
                invocation_text=invocation_text,
                summary_context_header=policy.summary_context_header,
            ),
            revision=loaded.revision,
            has_source_summary=snapshot.summary is not None,
            policy=policy,
            tools=tools,
            summary_model=summary_model,
        )

    def prepare_query(self, query_index: int) -> tuple[Message, ...]:
        """Return the current immutable request view for one model query."""

        if query_index < 0:
            raise ValueError("query_index must not be negative")
        frozen = tuple(dict(message) for message in self._messages)
        if not frozen or frozen[0].get("role") != "system":
            raise RuntimeError("Owner context must start with a System Prompt")
        estimate = count_tokens(
            self._policy.model_name,
            frozen,
            self._tools,
        )
        if (
            estimate / self._policy.capacity_tokens
            < self._policy.compaction_threshold
        ):
            return frozen

        attempt = ContextCompactionAttempt(
            compaction_id=uuid4().hex,
            query_index=query_index,
            estimated_tokens=estimate,
            capacity_tokens=self._policy.capacity_tokens,
            compaction_threshold=self._policy.compaction_threshold,
        )
        attempt_revision = self._revision
        self._record_notice(
            attempt.notice(ContextCompactionPhase.STARTED),
            revision=attempt_revision,
        )
        try:
            draft = generate_summary(
                frozen,
                has_source_summary=self._has_source_summary,
                policy=self._policy,
                tools=self._tools,
                model=self._summary_model,
            )
            committed = self._runtime.commit_summary(
                self._runtime_context,
                self._activation,
                expected_revision=attempt_revision,
                query_index=query_index,
                draft=draft,
            )
        except Exception as error:
            self._record_notice(
                attempt.notice(
                    ContextCompactionPhase.FAILED,
                    failure_type=type(error).__name__,
                ),
                revision=attempt_revision,
            )
            return frozen

        summary = committed.summary
        self._revision = committed.revision
        self._has_source_summary = True
        self._messages = [
            dict(frozen[0]),
            *render_summary(summary, self._policy.summary_context_header),
        ]
        self._record_notice(
            attempt.notice(
                ContextCompactionPhase.COMPLETED,
                summary_id=summary.summary_id,
            ),
            revision=attempt_revision,
        )
        return tuple(dict(message) for message in self._messages)

    def append(self, messages: Sequence[Message]) -> tuple[Message, ...]:
        """Persist messages, then advance the in-memory view and revision."""

        appended = tuple(dict(message) for message in messages)
        if not appended:
            return ()
        revision = self._runtime.append_messages(
            self._runtime_context,
            appended,
            expected_revision=self._revision,
        )
        self._messages.extend(appended)
        self._revision = revision
        return appended

    def _record_notice(
        self,
        notice: ContextCompactionNotice,
        *,
        revision: object,
    ) -> None:
        self._runtime.record_compaction(
            self._runtime_context,
            self._activation,
            notice,
            revision=revision,
        )
