"""Model-visible invocation facts shared by Agent implementations."""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any


class PromptRole(StrEnum):
    """Configured identities for every model-visible Agent role."""

    PROJECT_OWNER = "project_owner"
    HISTORICAL_OWNER = "historical_owner"
    PLANNER = "planner"
    REVIEWER = "reviewer"
    PLAN_HARD_GATE = "plan_hard_gate"
    MILESTONE_HARD_GATE = "milestone_hard_gate"
    STAGE_EXECUTOR = "stage_executor"


@dataclass(frozen=True, slots=True)
class InvocationContract:
    """Runtime facts that configuration must never interpolate or replace."""

    role: PromptRole
    operation: str
    project_root: Path
    observation_skill: Path
    triage_id: str
    fixed_work_object: Mapping[str, object]
    workspace: Mapping[str, object]
    output_contract: Mapping[str, object]


def render_invocation(
    contract: InvocationContract,
    observation_instruction: str,
) -> str:
    """Render one stable locator from which an Agent observes Runtime facts."""

    envelope: dict[str, Any] = {
        "role": contract.role.value,
        "operation": contract.operation,
        "project_root": str(contract.project_root.resolve()),
        "observation_skill": str(contract.observation_skill),
        "triage_id": contract.triage_id,
        "fixed_work_object": dict(contract.fixed_work_object),
        "workspace": dict(contract.workspace),
        "output_contract": dict(contract.output_contract),
    }
    return "\n\n".join(
        (
            "AgentPlaneX invocation envelope (Runtime-provided identity):",
            json.dumps(envelope, ensure_ascii=False, indent=2),
            observation_instruction.strip(),
        )
    )
