"""Small Codex SDK transport shared by Agent roles and protected gates."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from threading import Thread
from typing import Any

from openai_codex import (
    ApprovalMode,
    Codex,
    CodexConfig,
    CodexError,
    InputItem,
    MentionInput,
    Sandbox,
    TextInput,
)

from agentplanex.domains import AgentCollaborationError


class CodexTransportError(AgentCollaborationError):
    """A known local Codex process, thread, or turn failure."""


class CodexTransportTimeout(CodexTransportError):
    """A Codex turn exceeded its configured blocking timeout."""


@dataclass(frozen=True, slots=True)
class CodexTurnRequest:
    """Infrastructure-only input for one bounded Codex turn."""

    thread_id: str | None
    workspace: Path
    developer_instructions: str
    message: str
    mentions: tuple[tuple[str, Path], ...]
    output_schema: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class CodexTurnResult:
    """Raw final response and identities returned by the SDK."""

    thread_id: str
    turn_id: str
    status: str
    final_response: str


@dataclass(frozen=True, slots=True)
class CodexTurnTransport:
    """Start/resume Codex threads without knowing any Agent business Contract."""

    executable: str | None
    model: str | None
    timeout_seconds: float
    response_limit: int

    def run(self, request: CodexTurnRequest) -> CodexTurnResult:
        """Run one turn in a writable Agent workspace and always close the SDK client."""
        client: Codex | None = None
        try:
            client = Codex(
                CodexConfig(
                    codex_bin=self.executable,
                    client_name="agentplanex",
                    client_title="AgentPlaneX",
                )
            )
            if request.thread_id is None:
                thread = client.thread_start(
                    approval_mode=ApprovalMode.deny_all,
                    cwd=str(request.workspace),
                    developer_instructions=request.developer_instructions,
                    model=self.model,
                    sandbox=Sandbox.workspace_write,
                    service_name="agentplanex-agent",
                )
            else:
                thread = client.thread_resume(
                    request.thread_id,
                    approval_mode=ApprovalMode.deny_all,
                    cwd=str(request.workspace),
                    developer_instructions=request.developer_instructions,
                    model=self.model,
                    sandbox=Sandbox.workspace_write,
                )
                if thread.id != request.thread_id:
                    raise CodexTransportError("Codex resumed a different thread")

            input_items: list[InputItem] = [TextInput(request.message)]
            input_items.extend(
                MentionInput(name=name, path=str(path))
                for name, path in request.mentions
            )
            turn = thread.turn(
                input_items,
                approval_mode=ApprovalMode.deny_all,
                cwd=str(request.workspace),
                model=self.model,
                output_schema=request.output_schema,
                sandbox=Sandbox.workspace_write,
            )
            result = self._run_with_timeout(turn)
            status = getattr(result.status, "value", None)
            if status != "completed":
                raise CodexTransportError(
                    f"Codex turn ended without completion: {status!r}"
                )
            final_response = result.final_response
            if not isinstance(final_response, str) or not final_response.strip():
                raise CodexTransportError("Codex returned an empty final response")
            if len(final_response.encode("utf-8")) > self.response_limit:
                raise CodexTransportError("Codex final response exceeds the configured limit")
            return CodexTurnResult(
                thread_id=thread.id,
                turn_id=result.id,
                status=status,
                final_response=final_response,
            )
        except CodexTransportError:
            raise
        except (CodexError, OSError, RuntimeError) as error:
            raise CodexTransportError(f"Codex turn failed: {error}") from error
        finally:
            if client is not None:
                client.close()

    def _run_with_timeout(self, handle: Any) -> Any:
        result_box: list[Any] = []
        error_box: list[BaseException] = []

        def consume() -> None:
            try:
                result_box.append(handle.run())
            except BaseException as error:  # delivered to the caller below
                error_box.append(error)

        worker = Thread(target=consume, name="agentplanex-codex-turn", daemon=True)
        worker.start()
        worker.join(self.timeout_seconds)
        if worker.is_alive():
            with suppress(Exception):
                handle.interrupt()
            worker.join(min(10.0, max(1.0, self.timeout_seconds)))
            raise CodexTransportTimeout(
                f"Codex turn timed out after {self.timeout_seconds:.2f}s"
            )
        if error_box:
            error = error_box[0]
            if isinstance(error, (CodexError, OSError, RuntimeError)):
                raise CodexTransportError(f"Codex turn failed: {error}") from error
            raise error
        if not result_box:
            raise CodexTransportError("Codex turn produced no result")
        return result_box[0]
