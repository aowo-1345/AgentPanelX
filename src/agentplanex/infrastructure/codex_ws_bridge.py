"""Bridge Codex SDK stdio traffic to a Codex WebSocket app-server."""

from __future__ import annotations

import sys
import threading

from websockets.sync.client import connect


def main() -> int:
    """Forward newline-delimited app-server messages in both directions."""
    if len(sys.argv) != 2 or not sys.argv[1].strip():
        print("usage: python -m agentplanex.infrastructure.codex_ws_bridge URL", file=sys.stderr)
        return 2

    with connect(sys.argv[1], max_size=None) as websocket:
        sender = threading.Thread(target=_send_stdin, args=(websocket,), daemon=True)
        sender.start()
        for message in websocket:
            if isinstance(message, bytes):
                message = message.decode("utf-8", errors="replace")
            sys.stdout.write(message)
            if not message.endswith("\n"):
                sys.stdout.write("\n")
            sys.stdout.flush()
    return 0


def _send_stdin(websocket: object) -> None:
    """Send SDK stdin lines to the WebSocket connection."""
    try:
        for line in sys.stdin:
            websocket.send(line)  # type: ignore[attr-defined]
    except (BrokenPipeError, OSError, RuntimeError):
        return


if __name__ == "__main__":
    raise SystemExit(main())
