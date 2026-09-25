"""Exercise the HTTP stream against a real Workspace and persisted Owner input."""

import asyncio
import json
from collections.abc import Callable
from pathlib import Path

import pytest
from starlette.types import Message, Scope

import agentplanex.bootstrap as bootstrap
import agentplanex.web.app as web_app
from agentplanex.services.web.conversation_hub import ConversationHub, ConversationSubscriber
from agentplanex.settings import DEFAULT_SETTINGS_PATH, load_settings
from tests.test_conversation_hub import _UnusedResponsesTransport


def test_stream_snapshot_patch_reconnect_and_disconnect_cleanup(
    initialize_git_project: Callable[[], Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        bootstrap, "create_responses_transport", lambda _: _UnusedResponsesTransport()
    )
    settings = load_settings(DEFAULT_SETTINGS_PATH)
    settings = settings.model_copy(
        update={
            "workspace": settings.workspace.model_copy(
                update={"data_home": tmp_path / "workspace"}
            ),
        }
    )
    workspace = bootstrap.create_workspace(settings, settings_path=DEFAULT_SETTINGS_PATH)
    monkeypatch.setattr(web_app, "create_workspace", lambda *_args, **_kwargs: workspace)
    app = web_app.create_app(settings)
    project = workspace.register_project(
        name="SSE test",
        repository_path=initialize_git_project(),
        main_branch="main",
    )
    feature = workspace.create_feature(project_id=project.project_id, name="SSE test")
    runtime = workspace.runtime_factory(feature.worktree_path)
    runtime.initialize()
    released: list[ConversationSubscriber] = []
    unsubscribe = ConversationHub.unsubscribe

    def record_unsubscribe(hub: ConversationHub, subscriber: ConversationSubscriber) -> None:
        unsubscribe(hub, subscriber)
        released.append(subscriber)

    monkeypatch.setattr(ConversationHub, "unsubscribe", record_unsubscribe)

    async def scenario() -> None:
        path = (
            f"/api/projects/{project.project_id}/features/{feature.triage_id}/conversation/stream"
        )
        scope: Scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "root_path": "",
            "query_string": b"",
            "headers": [],
        }
        incoming: asyncio.Queue[Message] = asyncio.Queue()
        outgoing: asyncio.Queue[Message] = asyncio.Queue()

        async def frame() -> tuple[str, dict[str, object]]:
            message = await asyncio.wait_for(outgoing.get(), 2)
            assert message["type"] == "http.response.body"
            event, data = message["body"].decode().strip().split("\n")
            return event.removeprefix("event: "), json.loads(data.removeprefix("data: "))

        async def connect() -> asyncio.Task[None]:
            task = asyncio.create_task(app(scope, incoming.get, outgoing.put))
            start = await asyncio.wait_for(outgoing.get(), 2)
            assert start["status"] == 200
            assert (b"content-type", b"text/event-stream; charset=utf-8") in start["headers"]
            return task

        stream = await connect()
        try:
            assert await frame() == (
                "snapshot",
                {
                    "cursor": 0,
                    "messages": [],
                    "activation_has_reply": False,
                },
            )
            await asyncio.to_thread(runtime.submit_message, "Visible via SSE")
            event, patch = await frame()
            assert event == "patch"
            assert patch["cursor"] == 1
            rows = patch["messages"]
            assert isinstance(rows, list)
            assert rows[0]["role"] == "user"
            assert rows[0]["content"] == "Visible via SSE"
            await incoming.put({"type": "http.disconnect"})
            await asyncio.wait_for(stream, 2)
            assert len(released) == 1

            stream = await connect()
            event, snapshot = await frame()
            assert event == "snapshot"
            assert snapshot["messages"] == rows
            assert snapshot["cursor"] == 1
            await incoming.put({"type": "http.disconnect"})
            await asyncio.wait_for(stream, 2)
            assert len(released) == 2
        finally:
            stream.cancel()
            await asyncio.gather(stream, return_exceptions=True)

    try:
        asyncio.run(scenario())
    finally:
        workspace.close()
