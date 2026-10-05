"""Workspace-owned conversation delivery using an explicit Feature Runtime."""

import asyncio
from pathlib import Path

from agentplanex.domains.conversation_event import ConversationMessageAppended
from agentplanex.project_owner_agent.context.models import MessageHistory
from agentplanex.services.web.conversation_hub import ConversationHub
from agentplanex.settings import DEFAULT_SETTINGS_PATH, load_settings
from tests.support import (
    FakeOwnerFactory,
    FakeStageFactory,
    FeatureRuntimeFactory,
    GitProjectBuilder,
    SQLiteResource,
)


def test_runtime_publishes_message_only_after_owner_input_commit(tmp_path: Path) -> None:
    project = GitProjectBuilder.build(tmp_path / "project")
    database = SQLiteResource.create(project.path)
    owner = FakeOwnerFactory.create()
    stage = FakeStageFactory.create()
    hub = ConversationHub()
    feature = FeatureRuntimeFactory.create(
        project=project,
        database=database,
        owner=owner,
        stage=stage,
        settings=load_settings(DEFAULT_SETTINGS_PATH),
        conversation_hub=hub,
    )

    async def scenario() -> None:
        try:
            triage_id = feature.runtime.initialize().triage_id
            subscriber = hub.subscribe(triage_id)
            state = feature.runtime.submit_message("hello")
            event = await asyncio.wait_for(subscriber.next(), timeout=1)
            assert state.status.value == "PENDING"
            assert isinstance(event, ConversationMessageAppended)
            assert event.history.sequence == 1
            assert event.history.message[-1] == {"role": "user", "content": "hello"}
        finally:
            feature.close()

    asyncio.run(scenario())


def _event(triage_id: str, sequence: int) -> ConversationMessageAppended:
    return ConversationMessageAppended(
        triage_id=triage_id,
        history=MessageHistory(
            project_owner_session_id="session-1",
            message_id=f"history-{sequence}",
            sequence=sequence,
            message=({"role": "assistant", "content": f"reply-{sequence}"},),
        ),
    )


def test_hub_broadcasts_to_each_feature_subscriber_and_cleans_up() -> None:
    async def scenario() -> None:
        hub = ConversationHub()
        first = hub.subscribe("feature-a")
        second = hub.subscribe("feature-a")
        other = hub.subscribe("feature-b")

        hub.publish(_event("feature-a", 1))

        assert await asyncio.wait_for(first.next(), timeout=1) == _event("feature-a", 1)
        assert await asyncio.wait_for(second.next(), timeout=1) == _event("feature-a", 1)
        try:
            await asyncio.wait_for(other.next(), timeout=0.05)
        except TimeoutError:
            pass
        else:
            raise AssertionError("events crossed Feature boundaries")

        hub.unsubscribe(first)
        hub.publish(_event("feature-a", 2))
        assert await asyncio.wait_for(second.next(), timeout=1) == _event("feature-a", 2)
        assert await asyncio.wait_for(first.next(), timeout=1) is None

        waiting = asyncio.create_task(second.next())
        hub.close()
        assert await asyncio.wait_for(waiting, timeout=1) is None
        assert await asyncio.wait_for(other.next(), timeout=1) is None

    asyncio.run(scenario())
