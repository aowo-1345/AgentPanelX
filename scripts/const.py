"""Local-only PyCharm debug settings for AgentPlaneX."""

import os
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "settings.yaml"

# Default Project Owner playground used by the local debug launcher.
TARGET_PROJECT = PROJECT_ROOT / ".agentplanex" / "my-project"

# Set this to False when PyCharm already provides the required environment.
LOAD_DIRENV = True


def _load_direnv() -> None:
    """Import the approved repository .envrc environment into this process."""
    if not LOAD_DIRENV:
        return
    try:
        result = subprocess.run(
            ["direnv", "exec", str(PROJECT_ROOT), "env", "-0"],
            check=False,
            capture_output=True,
        )
    except OSError:
        return
    if result.returncode != 0:
        return
    for item in result.stdout.split(b"\0"):
        if b"=" not in item:
            continue
        key, value = item.split(b"=", 1)
        os.environ.setdefault(
            key.decode("utf-8", errors="replace"),
            value.decode("utf-8", errors="replace"),
        )


_load_direnv()
os.environ.setdefault("AGENTPLANEX_CONFIG", str(CONFIG_PATH))
