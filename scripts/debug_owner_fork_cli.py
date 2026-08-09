"""Inspect and interrogate a Historical Project Owner Fork without Runtime writes."""

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TextIO

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from const import TARGET_PROJECT  # noqa: E402

from agentplanex.domains import RestoredOwnerContext  # noqa: E402
from agentplanex.infrastructure.sqlite import (  # noqa: E402
    SQLiteDatabase,
    verify_schema,
)
from agentplanex.project_owner_agent.models.jbb import (  # noqa: E402
    JBBModel,
    OpenAIResponsesTransport,
)
from agentplanex.services.agent_contracts import AgentPromptCatalog  # noqa: E402
from agentplanex.services.historical_owner import (  # noqa: E402
    HistoricalOwnerForkService,
)
from agentplanex.services.owner_context import ProjectOwnerContextQuery  # noqa: E402
from agentplanex.settings import load_settings  # noqa: E402

type InputReader = Callable[[str], str]


def main(
    argv: Sequence[str] | None = None,
    *,
    read_input: InputReader = input,
    stdout: TextIO | None = None,
) -> int:
    args = _parser().parse_args(argv)
    output = stdout if stdout is not None else sys.stdout
    database = SQLiteDatabase.for_project(args.cwd.resolve())
    try:
        settings = load_settings()
        prompts = AgentPromptCatalog(settings.runtime.prompts)
        verify_schema(database)
        contexts = ProjectOwnerContextQuery(database, prompts.summary_context_header)
    except Exception as error:
        _print_error("database", error, output)
        return 1

    if args.print_context:
        try:
            restored = contexts.restore(
                args.message_id,
                summary_id=args.summary_id,
            )
        except Exception as error:
            _print_error("context", error, output)
            return 1
        _print_context(restored, output)
        return 0

    try:
        model_settings = settings.project_owner_agent.selected_model
        model = JBBModel(
            model=model_settings.name,
            tools=None,
            base_url=model_settings.base_url,
            timeout_seconds=model_settings.timeout_seconds,
            transport=OpenAIResponsesTransport(
                base_url=model_settings.base_url,
                timeout_seconds=model_settings.timeout_seconds,
                api_key_env=model_settings.api_key_env,
                http_headers=model_settings.http_headers,
                reasoning_effort=model_settings.reasoning_effort,
                service_tier=model_settings.service_tier,
            ),
        )
        fork = HistoricalOwnerForkService(contexts, prompts).open(
            args.message_id,
            summary_id=args.summary_id,
            model=model,
            model_name=model_settings.name,
        )
    except Exception as error:
        _print_error("fork-open", error, output)
        return 1

    print(
        json.dumps(
            {
                "action": "fork-opened",
                "ok": True,
                "model": {"name": fork.model_name, "tools": []},
                "fidelity": {
                    "message_checkpoint": fork.fidelity.message_checkpoint,
                    "summary_selection": fork.fidelity.summary_selection,
                    "agent_definition": fork.fidelity.agent_definition,
                    "model": fork.fidelity.model,
                },
                "context": _restored_context_json(fork.context),
            },
            ensure_ascii=False,
        ),
        file=output,
    )
    while True:
        try:
            question = read_input("investigator> ").strip()
        except EOFError:
            return 0
        if question in {"/exit", "/quit"}:
            return 0
        if not question:
            continue
        try:
            exchange = fork.ask(question)
        except Exception as error:
            _print_error("fork-ask", error, output)
            return 1
        print(
            json.dumps(
                {
                    "action": "fork-answer",
                    "ok": True,
                    "turn": exchange.turn,
                    "question": exchange.question,
                    "answer": exchange.answer,
                },
                ensure_ascii=False,
            ),
            file=output,
        )


def _print_context(restored: RestoredOwnerContext, output: TextIO) -> None:
    print(
        json.dumps(
            {
                "action": "context",
                "ok": True,
                "context": _restored_context_json(restored),
            },
            ensure_ascii=False,
        ),
        file=output,
    )


def _restored_context_json(restored: RestoredOwnerContext) -> dict[str, object]:
    return {
        "triage_id": restored.triage_id,
        "project_owner_session_id": restored.project_owner_session_id,
        "through": {
            "message_id": restored.through_message_id,
            "sequence": restored.through_sequence,
        },
        "summary": (
                {
                    "summary_id": restored.summary_id,
                    "intent_summary_content": restored.intent_summary_content,
                    "trajectory_summary_content": (
                        restored.trajectory_summary_content
                    ),
                "covered_through_message_id": restored.covered_through_message_id,
                "covered_through_sequence": restored.covered_through_sequence,
            }
            if restored.summary_id is not None
            else None
        ),
        "system_prompt": restored.system_prompt,
        "tools": list(restored.tools),
        "messages": list(restored.messages),
    }


def _print_error(action: str, error: Exception, output: TextIO) -> None:
    print(
        json.dumps(
            {
                "action": action,
                "ok": False,
                "error": f"{type(error).__name__}: {error}",
            },
            ensure_ascii=False,
        ),
        file=output,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect or interrogate a read-only Historical Project Owner Fork"
    )
    parser.add_argument("--cwd", type=Path, default=TARGET_PROJECT)
    parser.add_argument("--message-id", required=True)
    parser.add_argument("--summary-id")
    parser.add_argument(
        "--print-context",
        action="store_true",
        help="print the restored context and exit without constructing a model",
    )
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
