"""Credentialed Model Gateway journey through the real Project Runtime."""

import os
import re
from pathlib import Path

import pytest

from agentplanex.bootstrap import (
    create_project_runtime_control,
    create_project_workspace_query,
    create_responses_transport,
)
from agentplanex.infrastructure.logging import configure_logging
from agentplanex.settings import DEFAULT_SETTINGS_PATH, load_settings
from tests.fixtures import initialize_git_project

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CACHE_USAGE = re.compile(r"(?:^| )cached_tokens=(\d+)(?: |$)")


@pytest.mark.live_model
@pytest.mark.e2e
def test_codex_subscription_completes_a_cached_owner_tool_journey(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise human message, native Tool output, reply, cache, and continuation."""

    if os.getenv("AGENTPLANEX_RUN_LIVE_MODEL") != "1":
        pytest.skip("set AGENTPLANEX_RUN_LIVE_MODEL=1 to run credentialed QA")
    if not os.getenv("CLIPROXY_API_KEY", "").strip():
        pytest.skip("CLIPROXY_API_KEY is required for credentialed QA")

    configured = load_settings(_PROJECT_ROOT / DEFAULT_SETTINGS_PATH)
    settings = configured.model_copy(
        update={
            "project_owner_agent": configured.project_owner_agent.model_copy(
                update={"active_model": "codex"}
            )
        }
    )
    project_path = initialize_git_project(tmp_path / "live-project")
    monkeypatch.chdir(tmp_path)
    log_directory = tmp_path / ".logs"
    configure_logging(log_directory)
    gateway = create_responses_transport(settings)
    runtime = create_project_runtime_control(
        project_path=project_path,
        approval_mode="yolo",
        settings=settings,
        responses_transport=gateway,
    )

    try:
        runtime.initialize()
        first_activation = runtime.submit_message(
            "Use Bash to run `printf 'gateway-live-e2e-ok\\n'` exactly once. "
            "After observing the result, reply with `gateway-live-e2e-complete`. "
            "Do not modify files and do not use another tool."
        )
        first = runtime.drive_owner_model()

        if (
            first.exit is None
            or first.exit.content is None
            or "gateway-live-e2e-complete" not in first.exit.content
        ):
            pytest.fail("first live activation did not produce the expected safe marker")
        first_conversation = create_project_workspace_query(project_path=project_path).get(
            first_activation.triage_id
        ).conversation
        tool_messages = [message for message in first_conversation if message.role == "tool"]
        assert tool_messages
        assert tool_messages[-1].tool_activity is not None
        assert tool_messages[-1].tool_activity.name == "bash"
        assert tool_messages[-1].tool_activity.status == "completed"

        second_activation = runtime.submit_message(
            "Reply with the marker printed by Bash in the previous activation. "
            "Do not call a tool."
        )
        second = runtime.drive_owner_model()

        if (
            second.exit is None
            or second.exit.content is None
            or "gateway-live-e2e-ok" not in second.exit.content
        ):
            pytest.fail("continued live activation did not recall the safe marker")
        second_conversation = create_project_workspace_query(project_path=project_path).get(
            second_activation.triage_id
        ).conversation
        assert len(second_conversation) > len(first_conversation)

        log_files = list(log_directory.glob("agentplanex-*.log"))
        assert len(log_files) == 1
        log_lines = log_files[0].read_text(encoding="utf-8").splitlines()
        gateway_lines = [
            line
            for line in log_lines
            if "event=model_gateway_call" in line and "adapter=openai" in line
        ]
        assert len(gateway_lines) >= 3
        assert all("status=succeeded" in line for line in gateway_lines)
        cached_tokens = [
            int(match.group(1))
            for line in gateway_lines
            if (match := _CACHE_USAGE.search(line)) is not None
        ]
        assert any(tokens > 0 for tokens in cached_tokens)
    finally:
        gateway.close()
