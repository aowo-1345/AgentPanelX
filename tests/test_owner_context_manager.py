"""Behavioral tests for the Project Owner Agent context seam."""

from dataclasses import dataclass
from datetime import UTC, datetime

from agentplanex.domains.project_runtime_state import ProjectRuntimeState
from agentplanex.project_owner_agent.context.compaction import (
    ContextCompactionNotice,
    ContextCompactionPhase,
    OwnerContextPolicy,
    SummaryDraft,
)
from agentplanex.project_owner_agent.context.manager import (
    CommittedOwnerSummary,
    LoadedOwnerContext,
    OwnerContextManager,
    OwnerContextSnapshot,
)
from agentplanex.project_owner_agent.context.models import MessageHistory, SummaryHistory
from agentplanex.project_owner_agent.contracts import Message
from agentplanex.project_owner_agent.tools import (
    NoToolArguments,
    ToolCatalog,
    ToolDefinition,
)
from agentplanex.services.project_runtime_context.models import (
    OwnerActivation,
    OwnerActivationMode,
    OwnerActivationStatus,
    ProjectOwnerTaskType,
)


@dataclass(frozen=True, slots=True)
class _Revision:
    message_id: str
    summary_id: str | None


class _InMemoryContextRuntime:
    def __init__(self, snapshot: OwnerContextSnapshot) -> None:
        self.snapshot = snapshot
        self.revision = _Revision(
            message_id=snapshot.through_message_id,
            summary_id=(
                snapshot.summary.summary_id
                if snapshot.summary is not None
                else None
            ),
        )
        self.appended: list[tuple[Message, ...]] = []
        self.notices: list[ContextCompactionNotice] = []
        self.notice_revisions: list[object] = []
        self.committed: list[SummaryDraft] = []

    def load_context(
        self,
        _context: ProjectRuntimeState,
        _activation: OwnerActivation,
    ) -> LoadedOwnerContext:
        return LoadedOwnerContext(snapshot=self.snapshot, revision=self.revision)

    def append_messages(
        self,
        _context: ProjectRuntimeState,
        appended: tuple[Message, ...],
        *,
        expected_revision: object,
    ) -> object:
        assert expected_revision == self.revision
        self.appended.append(appended)
        self.revision = _Revision(
            message_id="message-after-append",
            summary_id=self.revision.summary_id,
        )
        return self.revision

    def commit_summary(
        self,
        _context: ProjectRuntimeState,
        _activation: OwnerActivation,
        *,
        expected_revision: object,
        query_index: int,
        draft: SummaryDraft,
    ) -> CommittedOwnerSummary:
        assert query_index == 0
        assert expected_revision == self.revision
        self.committed.append(draft)
        revision = self.revision
        summary = SummaryHistory(
            project_owner_session_id=self.snapshot.project_owner_session_id,
            summary_id="summary-committed",
            covered_through_message_id=revision.message_id,
            intent_summary_content=draft.intent_summary_content,
            trajectory_summary_content=draft.trajectory_summary_content,
        )
        self.revision = _Revision(
            message_id=revision.message_id,
            summary_id=summary.summary_id,
        )
        return CommittedOwnerSummary(summary=summary, revision=self.revision)

    def record_compaction(
        self,
        _context: ProjectRuntimeState,
        _activation: OwnerActivation,
        notice: ContextCompactionNotice,
        *,
        revision: object,
    ) -> None:
        self.notices.append(notice)
        self.notice_revisions.append(revision)


class _SummaryModel:
    def __init__(self) -> None:
        self.requests: list[list[Message]] = []

    def text(self, messages: list[Message], *, tools: ToolCatalog) -> str:
        self.requests.append(messages)
        assert tools.provider_schemas()[0]["name"] == "bash"
        prompt = messages[-1]["content"]
        if prompt == "Summarize trajectory.":
            return "<trajectory-summary>Work reached the refactor.</trajectory-summary>"
        if prompt == "Summarize initial intent.":
            return "<intent-summary>Build the clean context seam.</intent-summary>"
        raise AssertionError(f"Unexpected summary prompt: {prompt!r}")


def _tools() -> ToolCatalog:
    return ToolCatalog(
        (
            ToolDefinition(
                name="bash",
                description="Run a command.",
                arguments_type=NoToolArguments,
            ),
        )
    )


def _policy(*, capacity_tokens: int) -> OwnerContextPolicy:
    return OwnerContextPolicy(
        model_name="test-model",
        capacity_tokens=capacity_tokens,
        compaction_threshold=0.8,
        summary_context_header="Recovered rolling context.",
        trajectory_summary_prompt="Summarize trajectory.",
        initial_intent_summary_prompt="Summarize initial intent.",
        update_intent_summary_prompt="Update intent summary.",
    )


def test_manager_restores_one_model_view_and_keeps_appended_messages() -> None:
    """The Agent owns rendering while the Runtime only supplies raw facts."""

    summary = SummaryHistory(
        project_owner_session_id="owner-session",
        summary_id="summary-1",
        covered_through_message_id="message-1",
        intent_summary_content="Keep the approved architecture.",
        trajectory_summary_content="The initial design discussion is complete.",
    )
    snapshot = OwnerContextSnapshot(
        triage_id="triage-1",
        project_owner_session_id="owner-session",
        through_message_id="message-2",
        through_sequence=2,
        system_prompt="You are the persisted Project Owner.",
        tools=("bash",),
        summary=summary,
        covered_through_sequence=1,
        message_history=(
            MessageHistory(
                project_owner_session_id="owner-session",
                message_id="message-2",
                sequence=2,
                message=(
                    {"role": "system", "content": "must not be repeated"},
                    {"role": "user", "content": "Continue the refactor."},
                ),
            ),
        ),
    )
    runtime = _InMemoryContextRuntime(snapshot)
    context = ProjectRuntimeState(triage_id="triage-1")
    activation = OwnerActivation(
        activation_id="activation-1",
        triage_id="triage-1",
        task_type=ProjectOwnerTaskType.USER_INPUT,
        message_id="message-2",
        summary_id="summary-1",
        status=OwnerActivationStatus.RUNNING,
        driver_mode=OwnerActivationMode.MODEL,
        started_at=datetime.now(UTC),
    )
    manager = OwnerContextManager.restore(
        runtime=runtime,
        runtime_context=context,
        activation=activation,
        invocation_text=(
            "AgentPlaneX invocation envelope.\n"
            '"activation_id": "activation-1"\n'
            "Use the observation skill for current facts."
        ),
        policy=_policy(capacity_tokens=128_000),
        tools=_tools(),
        summary_model=_SummaryModel(),
    )

    restored = manager.prepare_query(query_index=0)
    assert [message.get("role") for message in restored] == [
        "system",
        "developer",
        "user",
        "user",
        "developer",
    ]
    assert restored[0]["content"] == "You are the persisted Project Owner."
    assert restored[1] == {
        "role": "developer",
        "content": "Recovered rolling context.",
    }
    assert restored[-2] == {"role": "user", "content": "Continue the refactor."}
    assert restored[-1] == {
        "role": "developer",
        "content": (
            "AgentPlaneX invocation envelope.\n"
            '"activation_id": "activation-1"\n'
            "Use the observation skill for current facts."
        ),
    }
    assert "must not be repeated" not in str(restored)
    assert sum(
        "AgentPlaneX invocation envelope" in str(message.get("content", ""))
        for message in restored
    ) == 1

    tool_turn = (
        {
            "type": "function_call",
            "call_id": "call-1",
            "name": "bash",
            "arguments": '{"command":"pwd"}',
        },
        {
            "type": "function_call_output",
            "call_id": "call-1",
            "output": "project-root",
        },
    )
    manager.append(tool_turn)

    continued = manager.prepare_query(query_index=1)
    assert continued[-3:-1] == tool_turn
    assert continued[-1] == restored[-1]
    assert runtime.appended == [tool_turn]


def test_manager_switches_only_after_runtime_commits_the_summary() -> None:
    snapshot = OwnerContextSnapshot(
        triage_id="triage-1",
        project_owner_session_id="owner-session",
        through_message_id="message-1",
        through_sequence=1,
        system_prompt="You are the persisted Project Owner.",
        tools=("bash",),
        summary=None,
        covered_through_sequence=None,
        message_history=(
            MessageHistory(
                project_owner_session_id="owner-session",
                message_id="message-1",
                sequence=1,
                message=(
                    {"role": "system", "content": "persisted system"},
                    {"role": "user", "content": "large context " * 100},
                ),
            ),
        ),
    )
    runtime = _InMemoryContextRuntime(snapshot)
    context = ProjectRuntimeState(triage_id="triage-1")
    activation = OwnerActivation(
        activation_id="activation-1",
        triage_id="triage-1",
        task_type=ProjectOwnerTaskType.USER_INPUT,
        message_id="message-1",
        status=OwnerActivationStatus.RUNNING,
        driver_mode=OwnerActivationMode.MODEL,
        started_at=datetime.now(UTC),
    )
    manager = OwnerContextManager.restore(
        runtime=runtime,
        runtime_context=context,
        activation=activation,
        invocation_text="AgentPlaneX owner invocation.",
        policy=_policy(capacity_tokens=1),
        tools=_tools(),
        summary_model=_SummaryModel(),
    )

    compacted = manager.prepare_query(query_index=0)

    assert runtime.committed == [
        SummaryDraft(
            intent_summary_content="Build the clean context seam.",
            trajectory_summary_content="Work reached the refactor.",
        )
    ]
    assert [notice.phase for notice in runtime.notices] == [
        ContextCompactionPhase.STARTED,
        ContextCompactionPhase.COMPLETED,
    ]
    assert [message.get("role") for message in compacted] == [
        "system",
        "developer",
        "user",
        "developer",
    ]
    assert "large context" not in str(compacted)
    assert "Build the clean context seam" in str(compacted)
