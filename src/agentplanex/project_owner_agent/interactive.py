"""Interactive confirmation layered on the default Agent."""

from agentplanex.project_owner_agent.agent import (
    AgentConfig,
    DefaultAgent,
)
from agentplanex.project_owner_agent.approval import Approval
from agentplanex.project_owner_agent.context.manager import OwnerContextManager
from agentplanex.project_owner_agent.contracts import (
    Action,
    AgentToolExecutor,
    Message,
    ToolExecutionResult,
)
from agentplanex.project_owner_agent.models.base import Model


class InteractiveAgent(DefaultAgent):
    def __init__(
        self,
        model: Model,
        execute_tool: AgentToolExecutor,
        *,
        owner_context: OwnerContextManager,
        approval: Approval,
        config: AgentConfig,
    ) -> None:
        super().__init__(
            model,
            execute_tool,
            owner_context=owner_context,
            config=config,
        )
        self.approval = approval

    def execute_actions(
        self,
        message: Message,
    ) -> list[Message]:
        self.add_messages(message)
        extra = message.get("extra")
        raw_actions = extra.get("actions", []) if isinstance(extra, dict) else []
        actions: list[Action] = [
            action for action in raw_actions if isinstance(action, dict)
        ]
        rejection = self.approval.review(actions)

        results: list[ToolExecutionResult]
        if rejection is None:
            results = [self.execute_tool(action) for action in actions]
        else:
            results = [
                ToolExecutionResult(
                    output={
                        "output": "",
                        "returncode": -1,
                        "exception_info": (
                            "The user rejected this action. "
                            f"Feedback: {rejection}"
                        ),
                    }
                )
                for _ in actions
            ]

        return self._record_action_results(message, results)
