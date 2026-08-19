"""The Project Owner Agent's model-context interface."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4

from agentplanex.agent_contracts import InvocationContract
from agentplanex.domains import (
    Message,
    MessageHistory,
    OwnerActivation,
    ProjectRuntimeContext,
    SummaryHistory,
)
from agentplanex.project_owner_agent.context.compaction import (
    ContextCompactionNotice,
    ContextCompactionPhase,
    OwnerContextPolicy,
    SummaryDraft,
    SummaryModel,
    count_tokens,
    generate_summary,
)
from agentplanex.project_owner_agent.context.rendering import render_owner_context, render_summary
from agentplanex.project_owner_agent.tools import ToolCatalog


@dataclass(frozen=True, slots=True)
class OwnerContextRevision:
    """A Runtime-issued position that the Agent carries without interpreting."""

    message_id: str
    summary_id: str | None

    def __post_init__(self) -> None:
        if not self.message_id.strip():
            raise ValueError("message_id must not be empty")
        if self.summary_id is not None and not self.summary_id.strip():
            raise ValueError("summary_id must not be empty")


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
    revision: OwnerContextRevision

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


class OwnerContextRuntime(Protocol):
    """Runtime effects required by one live Owner context."""

    def load_context(
        self,
        context: ProjectRuntimeContext,
        activation: OwnerActivation,
    ) -> OwnerContextSnapshot: ...

    def append_messages(
        self,
        context: ProjectRuntimeContext,
        appended: tuple[Message, ...],
    ) -> OwnerContextRevision: ...

    def commit_summary(
        self,
        context: ProjectRuntimeContext,
        activation: OwnerActivation,
        *,
        expected_revision: OwnerContextRevision,
        query_index: int,
        draft: SummaryDraft,
    ) -> SummaryHistory: ...

    def record_compaction(
        self,
        context: ProjectRuntimeContext,
        activation: OwnerActivation,
        notice: ContextCompactionNotice,
    ) -> None: ...


class OwnerContextManager:
    """Own one live Owner's model-visible context and persisted revision."""

    def __init__(
        self,
        *,
        runtime: OwnerContextRuntime,
        runtime_context: ProjectRuntimeContext,
        activation: OwnerActivation,
        messages: Sequence[Message],
        revision: OwnerContextRevision,
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

    @classmethod
    def restore(
        cls,
        *,
        runtime: OwnerContextRuntime,
        runtime_context: ProjectRuntimeContext,
        activation: OwnerActivation,
        invocation: InvocationContract,
        observation_instruction: str,
        policy: OwnerContextPolicy,
        tools: ToolCatalog,
        summary_model: SummaryModel,
    ) -> "OwnerContextManager":
        """Load raw checkpoint facts and render the initial model view."""

        snapshot = runtime.load_context(runtime_context, activation)
        if snapshot.triage_id != runtime_context.triage_id:
            raise RuntimeError("Restored Owner context does not match Runtime context")
        if snapshot.through_message_id != activation.message_id:
            raise RuntimeError("Restored Owner context does not match Activation message")
        selected_summary_id = (
            snapshot.summary.summary_id if snapshot.summary is not None else None
        )
        if selected_summary_id != activation.summary_id:
            raise RuntimeError("Restored Owner context does not match Activation Summary")
        if invocation.triage_id != runtime_context.triage_id:
            raise RuntimeError("Owner invocation does not match Runtime context")

        return cls(
            runtime=runtime,
            runtime_context=runtime_context,
            activation=activation,
            messages=render_owner_context(
                system_prompt=snapshot.system_prompt,
                summary=snapshot.summary,
                message_history=snapshot.message_history,
                invocation=invocation,
                observation_instruction=observation_instruction,
                summary_context_header=policy.summary_context_header,
            ),
            revision=snapshot.revision,
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

        compaction_id = uuid4().hex
        covered_through_message_id = self._revision.message_id
        self._record_notice(
            ContextCompactionPhase.STARTED,
            compaction_id=compaction_id,
            query_index=query_index,
            covered_through_message_id=covered_through_message_id,
            estimated_tokens=estimate,
            capacity_tokens=self._policy.capacity_tokens,
            compaction_threshold=self._policy.compaction_threshold,
        )
        try:
            draft = generate_summary(
                frozen,
                has_source_summary=self._revision.summary_id is not None,
                policy=self._policy,
                tools=self._tools,
                model=self._summary_model,
            )
            summary = self._runtime.commit_summary(
                self._runtime_context,
                self._activation,
                expected_revision=self._revision,
                query_index=query_index,
                draft=draft,
            )
            if summary.covered_through_message_id != self._revision.message_id:
                raise RuntimeError("Committed Summary does not match context revision")
        except Exception as error:
            self._record_notice(
                ContextCompactionPhase.FAILED,
                compaction_id=compaction_id,
                query_index=query_index,
                covered_through_message_id=covered_through_message_id,
                estimated_tokens=estimate,
                capacity_tokens=self._policy.capacity_tokens,
                compaction_threshold=self._policy.compaction_threshold,
                failure_type=type(error).__name__,
            )
            return frozen

        self._revision = OwnerContextRevision(
            message_id=self._revision.message_id,
            summary_id=summary.summary_id,
        )
        self._messages = [
            dict(frozen[0]),
            *render_summary(summary, self._policy.summary_context_header),
        ]
        self._record_notice(
            ContextCompactionPhase.COMPLETED,
            compaction_id=compaction_id,
            query_index=query_index,
            covered_through_message_id=covered_through_message_id,
            estimated_tokens=estimate,
            capacity_tokens=self._policy.capacity_tokens,
            compaction_threshold=self._policy.compaction_threshold,
            summary_id=summary.summary_id,
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
        )
        self._messages.extend(appended)
        self._revision = revision
        return appended

    def _record_notice(
        self,
        phase: ContextCompactionPhase,
        *,
        compaction_id: str,
        query_index: int,
        covered_through_message_id: str,
        estimated_tokens: int,
        capacity_tokens: int,
        compaction_threshold: float,
        summary_id: str | None = None,
        failure_type: str | None = None,
    ) -> None:
        self._runtime.record_compaction(
            self._runtime_context,
            self._activation,
            ContextCompactionNotice(
                phase=phase,
                compaction_id=compaction_id,
                query_index=query_index,
                covered_through_message_id=covered_through_message_id,
                estimated_tokens=estimated_tokens,
                capacity_tokens=capacity_tokens,
                compaction_threshold=compaction_threshold,
                summary_id=summary_id,
                failure_type=failure_type,
            ),
        )
