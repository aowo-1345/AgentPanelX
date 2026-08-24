"""Load stable External Agent Definitions from packaged resources."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from agentplanex.services.agent_invocation import (
    AgentInvocationError,
    resolve_packaged_skill,
)
from agentplanex.services.external_agent_runtime.models import (
    AgentDefinition,
    AgentSkill,
    ExecutionPolicy,
    SessionPolicy,
)
from agentplanex.settings import ExternalAgentDefinitionSettings

_INSTRUCTIONS_ROOT = Path(__file__).resolve().parents[2] / "resources" / "external_agents"
_COMMON_INSTRUCTIONS = _INSTRUCTIONS_ROOT / "common.md"


def build_agent_definition(
    agent_key: str,
    configured: ExternalAgentDefinitionSettings,
) -> AgentDefinition:
    """Resolve and integrity-bind one stable configured Agent."""
    instructions_path = _INSTRUCTIONS_ROOT / f"{configured.instructions}.md"
    try:
        common_instructions = _COMMON_INSTRUCTIONS.read_text(encoding="utf-8").strip()
        role_instructions = instructions_path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise ValueError(
            f"External Agent instructions are unavailable: {configured.instructions}"
        ) from error
    if not common_instructions:
        raise ValueError("External Agent common instructions are empty")
    if not role_instructions:
        raise ValueError(f"External Agent instructions are empty: {configured.instructions}")
    instructions = "\n\n".join((common_instructions, role_instructions))
    try:
        skills = tuple(
            AgentSkill(
                name=f"agentplanex-project-{name}",
                path=resolve_packaged_skill(f"agentplanex-project-{name}"),
            )
            for name in configured.skills
        )
    except AgentInvocationError as error:
        raise ValueError(str(error)) from error
    digest = hashlib.sha256(
        json.dumps(
            {
                "agent_key": agent_key,
                "configured": configured.model_dump(mode="json"),
                "instructions": instructions,
                "skills": [
                    {
                        "name": skill.name,
                        "files": [
                            {
                                "path": str(path.relative_to(skill.path.parent)),
                                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                            }
                            for path in sorted(skill.path.parent.rglob("*"))
                            if path.is_file()
                        ],
                    }
                    for skill in skills
                ],
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return AgentDefinition(
        agent_key=agent_key,
        stable_instructions=instructions,
        session_policy=SessionPolicy(configured.session_policy),
        bound_skills=skills,
        execution_policy=ExecutionPolicy(configured.execution_policy),
        allowed_operation_keys=configured.allowed_operations,
        protocol_digest=digest,
    )
