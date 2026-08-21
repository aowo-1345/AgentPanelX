"""Project Runtime execution for the Bash tool."""

from dataclasses import replace

from pydantic import Field

from agentplanex.domains import (
    BASH_TOOL_NAME,
    ProjectRuntimeState,
    RuntimeContextChangeReason,
    ToolExecutionResult,
)
from agentplanex.infrastructure import run_local_shell
from agentplanex.project_owner_agent.tools import (
    NonBlankText,
    ToolArgumentsModel,
    ToolDefinition,
)
from agentplanex.project_runtime.executions.base import (
    ProjectExecution,
    project_execution,
)

BASH_DESCRIPTION = (
    "Execute a Bash command with writes confined to the current Feature worktree, "
    "with .git and .agentplanex read-only and network access disabled. If the "
    "sandbox denies a required capability, do not retry or attempt a bypass; "
    "explain the required user action and return control to the user."
)


class BashArguments(ToolArgumentsModel):
    command: NonBlankText = Field(
        description="Bash command to run in the current Feature worktree."
    )


BASH_TOOL = ToolDefinition(
    name=BASH_TOOL_NAME,
    description=BASH_DESCRIPTION,
    arguments_type=BashArguments,
)


@project_execution(BASH_TOOL)
class BashExecution(ProjectExecution[BashArguments]):
    """Execute Bash commands within the bound project and runtime limits."""

    def execute(
        self,
        _context: ProjectRuntimeState,
        arguments: BashArguments,
    ) -> ToolExecutionResult:
        settings = self.dependencies.settings.bash
        output = run_local_shell(
            arguments.command,
            cwd=self.dependencies.project_path,
            timeout_seconds=settings.timeout_seconds,
            output_limit=settings.output_limit,
        )
        denied_capability = _sandbox_denied_capability(output)
        if denied_capability is not None:
            reason = (
                "The Bash command requires a capability denied by the Project Owner "
                f"sandbox: {denied_capability}."
            )

            def block(current: ProjectRuntimeState) -> ProjectRuntimeState:
                if current.status not in {"TODO", "IN_PROGRESS"}:
                    return current
                return replace(
                    current,
                    status="BLOCKED",
                    blocked_reason=reason,
                    blocked_capability=denied_capability,
                    blocked_previous_status=current.status,
                )

            blocked = self.dependencies.context.transition(
                reason=RuntimeContextChangeReason.USER_INTERVENTION_REQUIRED,
                mutate=block,
            )
            output.update(
                {
                    "error_type": "SANDBOX_POLICY_DENIED",
                    "blocked_capability": denied_capability,
                    "user_action_required": blocked.blocked_reason is not None,
                    "sandbox_policy": {
                        "writable_root": str(
                            self.dependencies.project_path.resolve()
                        ),
                        "protected_paths": [".git", ".agentplanex"],
                        "network": "disabled",
                    },
                    "guidance": (
                        "Do not retry or attempt to bypass the sandbox. Explain why "
                        "this capability is required, ask the user for help, and return "
                        "control without another tool call."
                    ),
                }
            )
        return ToolExecutionResult(output=output)


def _sandbox_denied_capability(output: dict[str, object]) -> str | None:
    if output.get("returncode") == 0:
        return None
    detail = " ".join(
        str(output.get(key, "")) for key in ("output", "exception_info")
    ).casefold()
    network_markers = (
        "network is unreachable",
        "could not resolve host",
        "temporary failure in name resolution",
        "failed to connect",
        "connection refused",
        "could not connect to server",
    )
    if any(marker in detail for marker in network_markers):
        return "network"
    filesystem_markers = (
        "read-only file system",
        "permission denied",
        "operation not permitted",
    )
    if any(marker in detail for marker in filesystem_markers):
        return "filesystem_outside_feature"
    return None
