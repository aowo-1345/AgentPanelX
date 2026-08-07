"""Shared, model-visible identity for AgentPlaneX invocations."""

import json
from collections.abc import Mapping
from pathlib import Path

from agentplanex.domains import AgentCollaborationError

OBSERVE_SKILL_NAME = "agentplanex-project-observe"
_PACKAGED_SKILL = (
    Path(__file__).resolve().parents[1]
    / "resources"
    / "skills"
    / OBSERVE_SKILL_NAME
    / "SKILL.md"
)


def resolve_observation_skill() -> Path:
    """Return the complete project-observation Skill shipped with AgentPlaneX."""

    detail = _PACKAGED_SKILL.parent / "references" / "detail.md"
    if _PACKAGED_SKILL.is_file() and detail.is_file():
        return _PACKAGED_SKILL
    raise AgentCollaborationError(
        f"Packaged {OBSERVE_SKILL_NAME} Skill is incomplete"
    )


def render_invocation_envelope(
    *,
    role: str,
    operation: str,
    project_root: Path,
    observation_skill: Path,
    triage_id: str,
    fixed_work_object: Mapping[str, object],
    workspace: str,
    output_contract: str,
) -> str:
    """Render the small locator from which an Agent observes authoritative facts."""

    envelope = {
        "role": role,
        "operation": operation,
        "project_root": str(project_root.resolve()),
        "observation_skill": str(observation_skill),
        "triage_id": triage_id,
        "fixed_work_object": dict(fixed_work_object),
        "workspace": workspace,
        "output_contract": output_contract,
    }
    return "\n\n".join(
        (
            "AgentPlaneX invocation envelope (Runtime-provided identity):",
            json.dumps(envelope, ensure_ascii=False, indent=2),
            f"When project facts or history are needed, read the complete "
            f"`{OBSERVE_SKILL_NAME}` SKILL.md at observation_skill and follow its "
            "referenced investigation path for this role. Runtime, Git, and artifacts "
            "remain authoritative; do not invent a get_project_view tool. Never replace "
            "a fixed work object with a newer current pointer.",
        )
    )
