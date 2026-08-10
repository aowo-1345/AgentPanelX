#!/usr/bin/env python3
"""Export the visible local Runtime into the read-only public Console snapshot."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any
from urllib.request import urlopen

DEFAULT_OUTPUT = Path("frontend/src/showcase/consoleSnapshot.json")
SECRET_PATTERNS = (
    re.compile(r"xox[baprs]-[A-Za-z0-9-]+"),
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
)
TRACE_PATTERNS = (
    re.compile(r"(?i)(request id:\s*)[A-Za-z0-9_-]+"),
    re.compile(r"(?i)(cf-ray:\s*)[A-Za-z0-9_-]+"),
)


def fetch_json(base_url: str, path: str) -> Any:
    with urlopen(f"{base_url.rstrip('/')}{path}", timeout=10) as response:
        return json.load(response)


def project_path_replacements(projects: list[dict[str, Any]]) -> tuple[tuple[str, str], ...]:
    replacements: list[tuple[str, str]] = []
    for project in projects:
        private = str(project.get("repository_path", "")).rstrip("/")
        if not private:
            continue
        display_name = str(project.get("name", "project")).strip() or "project"
        public_name = re.sub(r"[^\w.-]+", "-", display_name).strip("-") or "project"
        replacements.append((private, f"~/projects/{public_name}"))
    return tuple(sorted(set(replacements), key=lambda item: len(item[0]), reverse=True))


def sanitize_text(value: str, path_replacements: tuple[tuple[str, str], ...]) -> str:
    sanitized = value
    for private, public in path_replacements:
        sanitized = sanitized.replace(private, public)
    for pattern in SECRET_PATTERNS:
        sanitized = pattern.sub("[redacted]", sanitized)
    for pattern in TRACE_PATTERNS:
        sanitized = pattern.sub(r"\1[redacted]", sanitized)
    return sanitized


def sanitize(value: Any, path_replacements: tuple[tuple[str, str], ...]) -> Any:
    if isinstance(value, str):
        return sanitize_text(value, path_replacements)
    if isinstance(value, list):
        return [sanitize(item, path_replacements) for item in value]
    if isinstance(value, dict):
        return {key: sanitize(item, path_replacements) for key, item in value.items()}
    return value


def export_snapshot(base_url: str, output: Path) -> None:
    projects = fetch_json(base_url, "/api/projects")
    features = fetch_json(base_url, "/api/features")
    workspaces: dict[str, Any] = {}

    for feature in features:
        project_id = feature["project_id"]
        triage_id = feature["triage_id"]
        path = f"/api/projects/{project_id}/features/{triage_id}/workspace"
        workspaces[f"{project_id}:{triage_id}"] = fetch_json(base_url, path)

    snapshot = sanitize(
        {
            "projects": projects,
            "features": features,
            "workspaces": workspaces,
        },
        project_path_replacements(projects),
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(f"{output.suffix}.tmp")
    temporary.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(output)
    print(
        f"Exported {len(projects)} projects, {len(features)} features, "
        f"and {len(workspaces)} workspaces to {output}"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:13475")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args()
    export_snapshot(arguments.base_url, arguments.output)


if __name__ == "__main__":
    main()
