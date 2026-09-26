"""Small Codex SDK transport shared by Agent roles and protected gates."""

from __future__ import annotations

import codecs
import json
import os
import pty
import shutil
import socket
import subprocess
import sys
import termios
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from threading import Thread
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

from openai_codex import (
    ApprovalMode,
    Codex,
    CodexConfig,
    CodexError,
    InputItem,
    MentionInput,
    Sandbox,
    SkillInput,
    TextInput,
)


class CodexTransportError(RuntimeError):
    """A known local Codex process, thread, or turn failure."""


class CodexTransportTimeout(CodexTransportError):
    """A Codex turn exceeded its configured blocking timeout."""


class CodexTransportUnsafeTimeout(CodexTransportTimeout):
    """A timed-out Codex turn could not be confirmed terminated."""


@dataclass(frozen=True, slots=True)
class CodexTurnRequest:
    """Infrastructure-only input for one bounded Codex turn."""

    thread_id: str | None
    workspace: Path
    developer_instructions: str
    message: str
    mentions: tuple[tuple[str, Path], ...]
    skills: tuple[tuple[str, Path], ...] = ()
    output_schema: dict[str, Any] | None = None
    observer_key: str | None = None
    code_workspace: Path | None = None


@dataclass(frozen=True, slots=True)
class CodexTurnResult:
    """Raw final response and identities returned by the SDK."""

    thread_id: str
    turn_id: str
    status: str
    final_response: str


class _NativeCodexStageTerminal:
    """Run the official Codex TUI against the Stage's shared app-server."""

    def __init__(
        self,
        *,
        executable: str | None,
        workspace: Path,
        stage_run_id: str,
        output_sink: Callable[[str, str], None],
    ) -> None:
        self._executable = executable or shutil.which("codex")
        self._workspace = workspace
        self._stage_run_id = stage_run_id
        self._output_sink = output_sink
        self._server: subprocess.Popen[str] | None = None
        self._server_stderr: Thread | None = None
        self._tui: subprocess.Popen[bytes] | None = None
        self._pty_master: int | None = None
        self._reader: Thread | None = None
        self._url: str | None = None

    @property
    def sdk_launch_args(self) -> tuple[str, ...]:
        """Return the SDK launch command that proxies to the shared server."""
        if self._url is None:
            raise CodexTransportError("Native Codex app-server has not started")
        return (
            sys.executable,
            "-m",
            "agentplanex.infrastructure.codex_ws_bridge",
            self._url,
        )

    def start(
        self,
        *,
        config_overrides: tuple[str, ...],
    ) -> None:
        """Start one local WebSocket app-server for the SDK and TUI."""
        if not self._executable:
            raise CodexTransportError("The codex executable is not available")
        port = _free_tcp_port()
        self._url = f"ws://127.0.0.1:{port}"
        args = [self._executable]
        for override in config_overrides:
            args.extend(("--config", override))
        args.extend(("app-server", "--listen", self._url))
        self._server = subprocess.Popen(
            args,
            cwd=self._workspace,
            env=os.environ.copy(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        self._server_stderr = Thread(
            target=_drain_process_stderr,
            args=(self._server.stderr,),
            daemon=True,
            name="agentplanex-codex-app-server-stderr",
        )
        self._server_stderr.start()
        ready_url = f"http://127.0.0.1:{port}/readyz"
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            if self._server.poll() is not None:
                raise CodexTransportError("Codex app-server exited before becoming ready")
            try:
                with urlopen(ready_url, timeout=0.25) as response:
                    if response.status == 200:
                        return
            except (OSError, URLError):
                time.sleep(0.05)
        raise CodexTransportError("Codex app-server did not become ready")

    def attach(self, thread_id: str) -> None:
        """Attach the official read-only TUI to the current SDK thread."""
        if self._executable is None or self._url is None:
            raise CodexTransportError("Native Codex app-server is not available")
        master, slave = pty.openpty()
        termios.tcsetwinsize(slave, (30, 100))
        try:
            self._tui = subprocess.Popen(
                [
                    self._executable,
                    "resume",
                    thread_id,
                    "--remote",
                    self._url,
                    "--no-alt-screen",
                    "--ask-for-approval",
                    "never",
                ],
                cwd=self._workspace,
                env={**os.environ, "TERM": "xterm-256color", "COLORTERM": "truecolor"},
                stdin=slave,
                stdout=slave,
                stderr=slave,
                close_fds=True,
            )
            self._pty_master = master
            os.close(slave)
        except OSError:
            os.close(master)
            os.close(slave)
            raise
        self._reader = Thread(
            target=self._read_tui_output,
            daemon=True,
            name="agentplanex-codex-native-terminal",
        )
        self._reader.start()

    def close(self) -> None:
        """Stop the observer TUI and its dedicated app-server."""
        tui = self._tui
        if tui is not None and tui.poll() is None:
            tui.terminate()
            try:
                tui.wait(timeout=2)
            except subprocess.TimeoutExpired:
                tui.kill()
                tui.wait()
        master = self._pty_master
        if master is not None:
            with suppress(OSError):
                os.close(master)
            self._pty_master = None
        server = self._server
        if server is not None and server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=2)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()

    def _read_tui_output(self) -> None:
        master = self._pty_master
        if master is None:
            return
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        query_buffer = b""
        while True:
            try:
                data = os.read(master, 16_384)
            except OSError:
                return
            if not data:
                return
            # Only terminal capability replies are written; browser input is never forwarded.
            query_buffer += data
            for query, reply in (
                (b"\x1b[6n", b"\x1b[1;1R"),
                (b"\x1b[c", b"\x1b[?1;2c"),
                (b"\x1b[?u", b"\x1b[?0u"),
                (b"\x1b]10;?\x1b\\", b"\x1b]10;rgb:d4d4/d4d4/d8d8\x1b\\"),
                (b"\x1b]11;?\x1b\\", b"\x1b]11;rgb:0707/0808/0b0b\x1b\\"),
            ):
                if query in query_buffer:
                    with suppress(OSError):
                        os.write(master, reply)
                    query_buffer = query_buffer.replace(query, b"")
            query_buffer = query_buffer[-32:]
            self._output_sink(
                self._stage_run_id,
                decoder.decode(data),
            )


def _free_tcp_port() -> int:
    """Reserve an ephemeral local port for one app-server session."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _drain_process_stderr(stream: Any) -> None:
    """Prevent a long-running app-server stderr pipe from filling."""
    if stream is not None:
        for _line in stream:
            pass


@dataclass(frozen=True, slots=True)
class CodexTurnTransport:
    """Start/resume Codex threads without knowing any Agent business Contract."""

    executable: str | None
    model: str | None
    timeout_seconds: float
    response_limit: int
    network_access: bool = True
    event_sink: Callable[[str, str], None] | None = None

    def _config_overrides(self, workspace: Path) -> tuple[str, ...]:
        """Bind the shared Topos MCP server to this turn's target worktree."""
        workspace_text = json.dumps(str(workspace.resolve()), ensure_ascii=False)
        return (
            "sandbox_workspace_write.network_access="
            f"{str(self.network_access).lower()}",
            f"mcp_servers.topos.cwd={workspace_text}",
            f"mcp_servers.topos.env.TOPOS_MCP_FILE_ROOT={workspace_text}",
        )

    def run(
        self,
        request: CodexTurnRequest,
        *,
        on_thread_opened: Any | None = None,
    ) -> CodexTurnResult:
        """Run one turn in a writable Agent workspace and always close the SDK client."""
        client: Codex | None = None
        native_terminal: _NativeCodexStageTerminal | None = None
        try:
            config_overrides = self._config_overrides(
                request.code_workspace or request.workspace
            )
            if self.event_sink is not None and request.observer_key is not None:
                native_terminal = _NativeCodexStageTerminal(
                    executable=self.executable,
                    workspace=request.workspace,
                    stage_run_id=request.observer_key,
                    output_sink=self.event_sink,
                )
                native_terminal.start(config_overrides=config_overrides)
            client = Codex(
                CodexConfig(
                    codex_bin=self.executable,
                    launch_args_override=(
                        native_terminal.sdk_launch_args if native_terminal is not None else None
                    ),
                    client_name="agentplanex",
                    client_title="AgentPlaneX",
                    config_overrides=config_overrides,
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

            if on_thread_opened is not None:
                on_thread_opened(thread.id)
            if native_terminal is not None:
                native_terminal.attach(thread.id)

            input_items: list[InputItem] = [TextInput(request.message)]
            input_items.extend(
                SkillInput(name=name, path=str(path))
                for name, path in request.skills
            )
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
            if native_terminal is not None:
                native_terminal.close()

    def _run_with_timeout(
        self,
        handle: Any,
    ) -> Any:
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
            if worker.is_alive():
                raise CodexTransportUnsafeTimeout(
                    "Codex turn timed out and termination could not be confirmed"
                )
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
