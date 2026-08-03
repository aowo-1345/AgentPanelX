"""Launch an interactive Project Owner Agent against the local playground."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from const import TARGET_PROJECT  # noqa: E402

DEBUG_ARGS = [
    "--cwd",
    str(TARGET_PROJECT),
    "--mode",
    "confirm",
]


def main() -> int:
    TARGET_PROJECT.mkdir(parents=True, exist_ok=True)

    from agentplanex.cli import main as project_owner_cli_main

    return project_owner_cli_main([*DEBUG_ARGS, *sys.argv[1:]])


if __name__ == "__main__":
    raise SystemExit(main())
