"""Terminal confirmation for model-proposed action batches."""

import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol, TextIO

from agentplanex.domains import Action

type ApprovalMode = Literal["confirm", "yolo"]


class Approval(Protocol):
    def review(self, actions: list[Action]) -> str | None: ...


def _read_input(prompt: str) -> str:
    print(prompt, end="", file=sys.stderr, flush=True)
    return input()


@dataclass(frozen=True, slots=True)
class TerminalApproval:
    input_reader: Callable[[str], str] = _read_input
    output: TextIO = sys.stderr
    require_tty: bool = True

    def review(self, actions: list[Action]) -> str | None:
        if not actions:
            return None
        if self.require_tty and not _stdin_is_interactive():
            return "approval requires an interactive terminal"

        print("\nProject Owner proposes these tool actions:", file=self.output)
        print(json.dumps(actions, indent=2, ensure_ascii=False), file=self.output)
        answer = self.input_reader(
            "Press Enter to approve, or type rejection feedback: "
        ).strip()
        return answer or None


def _stdin_is_interactive() -> bool:
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except (OSError, ValueError):
        return False
