"""Minimal streaming chat CLI for the configured Project Owner gateway."""

from __future__ import annotations

import argparse
import os
import sys
from time import monotonic
from typing import Any

from openai import OpenAI

from agentplanex.settings import ModelSettings, load_settings
from scripts.const import CONFIG_PATH

type History = list[Any]


def _create_client() -> tuple[OpenAI, ModelSettings]:
    settings = load_settings(CONFIG_PATH)
    model = settings.project_owner_agent.selected_model
    api_key = os.getenv(model.api_key_env)
    if api_key is None or not api_key.strip():
        raise ValueError(
            f"Missing credentials: environment variable {model.api_key_env} is not set"
        )

    return (
        OpenAI(
            api_key=api_key,
            base_url=model.base_url,
            timeout=model.timeout_seconds,
            max_retries=0,
            default_headers=model.http_headers,
        ),
        model,
    )


def _run_turn(
    client: OpenAI,
    model: ModelSettings,
    history: History,
    prompt: str,
) -> bool:
    user_message = {"role": "user", "content": prompt}
    request: dict[str, Any] = {
        "model": model.name,
        "input": [*history, user_message],
        "store": False,
        "stream": True,
    }
    if model.reasoning_effort is not None:
        request["reasoning"] = {"effort": model.reasoning_effort}
    if model.service_tier is not None:
        request["service_tier"] = model.service_tier

    started_at = monotonic()
    first_event_at: float | None = None
    first_event_type: str | None = None
    completed_response: Any | None = None
    print("assistant> ", end="", flush=True)

    try:
        stream = client.responses.create(**request)
        try:
            for event in stream:
                if first_event_at is None:
                    first_event_at = monotonic()
                    first_event_type = event.type

                if event.type == "response.output_text.delta":
                    print(event.delta, end="", flush=True)
                elif event.type == "response.completed":
                    completed_response = event.response
                elif event.type in {"response.failed", "error"}:
                    print(f"\n{event}", file=sys.stderr, flush=True)
        finally:
            stream.close()
    except Exception as error:
        print()
        print(
            f"request_failed after={monotonic() - started_at:.3f}s: "
            f"{type(error).__name__}: {error}",
            file=sys.stderr,
        )
        return False

    print()
    elapsed = monotonic() - started_at
    if completed_response is None:
        print(
            f"request_failed after={elapsed:.3f}s: stream ended without response.completed",
            file=sys.stderr,
        )
        return False

    history.append(user_message)
    history.extend(completed_response.output)
    first_event_elapsed = (
        first_event_at - started_at if first_event_at is not None else elapsed
    )
    print(
        f"[first_event={first_event_type} after={first_event_elapsed:.3f}s "
        f"total={elapsed:.3f}s]",
        file=sys.stderr,
        flush=True,
    )
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a minimal stream=True chat with in-memory history."
    )
    parser.add_argument(
        "prompt",
        nargs="*",
        help="optional first message",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="send the command-line prompt once and report failure via exit status",
    )
    args = parser.parse_args(argv)
    pending = " ".join(args.prompt).strip()
    if args.once and not pending:
        print("--once requires a prompt", file=sys.stderr)
        return 2

    try:
        client, model = _create_client()
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2

    print(
        f"model={model.name} base_url={model.base_url} stream=True",
        file=sys.stderr,
        flush=True,
    )
    print("Enter /exit or /quit to stop.", file=sys.stderr)

    history: History = []
    try:
        while True:
            if not pending:
                try:
                    pending = input("> ").strip()
                except (EOFError, KeyboardInterrupt):
                    print()
                    return 0
            if not pending:
                continue
            if pending in {"/exit", "/quit"}:
                return 0

            try:
                succeeded = _run_turn(client, model, history, pending)
            except KeyboardInterrupt:
                print("\nrequest interrupted", file=sys.stderr)
                if args.once:
                    return 130
                succeeded = False
            if args.once:
                return 0 if succeeded else 1
            pending = ""
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
