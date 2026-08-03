"""Interactive confirmation layered on the default Agent."""

from agentplanex.domains import (
    Action,
    ActionOutput,
    ProjectRuntimeContext,
    ToolExecutor,
)
from agentplanex.project_owner_agent.agent import (
    AgentConfig,
    DefaultAgent,
    MessageAppender,
)
from agentplanex.project_owner_agent.approval import Approval
from agentplanex.project_owner_agent.models.base import Message, Model


class InteractiveAgent(DefaultAgent):
    def __init__(
        self,
        model: Model,
        execute_tool: ToolExecutor,
        *,
        append_messages: MessageAppender,
        initial_messages: list[Message],
        approval: Approval,
        config: AgentConfig,
    ) -> None:
        super().__init__(
            model,
            execute_tool,
            append_messages=append_messages,
            initial_messages=initial_messages,
            config=config,
        )
        self.approval = approval

    def execute_actions(
        self,
        context: ProjectRuntimeContext,
        message: Message,
    ) -> list[Message]:
        extra = message.get("extra")
        raw_actions = extra.get("actions", []) if isinstance(extra, dict) else []
        actions: list[Action] = [
            action for action in raw_actions if isinstance(action, dict)
        ]
        rejection = self.approval.review(actions)

        outputs: list[ActionOutput]
        if rejection is None:
            outputs = [self.execute_tool(context, action) for action in actions]
        else:
            outputs = [
                {
                    "output": "",
                    "returncode": -1,
                    "exception_info": (
                        f"The user rejected this action. Feedback: {rejection}"
                    ),
                }
                for _ in actions
            ]

        return self.add_messages(
            context,
            *self.model.format_observation_messages(message, outputs)
        )
