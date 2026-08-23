---
name: agentplanex-project-control
description: 通过真实 Runtime 有边界地介入 AgentPlaneX 项目。当开发 Agent 需要在不启动 Project Owner 模型的情况下手动驱动 Activation、发送 message、执行 approve、reject、start、drive-delivery，或逐步验证 Tool 副作用时使用；不要用于事后归因、只读历史解释、直接修改 SQLite 或 Git ref，也不要操作未经授权的真实项目。
---

# AgentPlaneX 项目介入

本 Skill 负责改变项目运行状态。只读定位先使用 `$agentplanex-project-observe`；事后归因由独立 Skill 负责，不在这里下结论。

开发与调试默认使用 `.agentplanex/tests/<case>` 下的隔离 Git 项目。确认目标 `--cwd`、当前角色、待处理 Activation 和用户授权后再执行操作。

## 控制入口

如果当前 Invocation 明确提供 `control_command_prefix`，它是本次运行的精确入口；在其后
追加下面示例中的命令文本，不要自行改回仓库脚本路径。以下 `scripts/debug_tool_cli.py`
形式用于源码仓库中的人工开发与调试。

所有写命令都经过特权 `ProjectRuntimeControl`，并复用正常 Runtime 的同一套
Context、Planning、Delivery、Execution、SQLite、Git 和 EventBus 规则。`view` 只构造
独立的只读 `ProjectControlQuery`，不会启动模型或取得 Feature operation lock：

```bash
uv run python scripts/debug_tool_cli.py --cwd <project> --print "view"
uv run python scripts/debug_tool_cli.py --cwd <project> --print "message <内容>"
uv run python scripts/debug_tool_cli.py --cwd <project> --print "approve"
uv run python scripts/debug_tool_cli.py --cwd <project> --print "reject <原因>"
uv run python scripts/debug_tool_cli.py --cwd <project> --print "start"
uv run python scripts/debug_tool_cli.py --cwd <project> --print "approve-blocked-run"
uv run python scripts/debug_tool_cli.py --cwd <project> --print "reject-blocked-run <原因>"
uv run python scripts/debug_tool_cli.py --cwd <project> --print "drive-delivery"
```

只执行当前 Runtime 允许且用户已经授权的命令。不要通过直接写 SQLite、修改 Git ref 或伪造 Timeline 绕过关卡。

## 手动驱动 Owner

开发 Agent 要亲自扮演 Project Owner 时，显式选择 Tool 驱动；这不会构造或调用 Owner 模型：

```bash
uv run python scripts/debug_tool_cli.py --cwd <project> --print \
  'drive tool {"tool":"update_milestones","arguments":{...}}'

uv run python scripts/debug_tool_cli.py --cwd <project> --print \
  'drive tool {"tool":"run_next_milestone","arguments":{}}'

uv run python scripts/debug_tool_cli.py --cwd <project> --print \
  "drive reply <给用户的回复>"
```

每条 Tool Action 都绑定同一个持久化 Activation，并写入 Owner message history。没有 `AgentExit` 时，Activation 以 `PENDING + TOOL` 等待下一步；有终止结果时自动进入 `COMPLETED` 或 `FAILED`。进程意外中断且无法继续时，显式执行 `drive fail <原因>`，不得直接修库。

`drive model` 才会启动真实 Owner 模型；无参数的 `drive` 是它的兼容别名。不要把 Activation 的 `MODEL/TOOL` 驱动模式与 `confirm/yolo` Tool 审批模式混为一谈。

裸 Tool Action JSON 只用于没有未完成 Activation 时的单工具隔离验证。存在 Activation 时必须使用 `drive tool`，Runtime 会拒绝旁路执行。

## 验证结果

每一步检查结构化结果中的 `ok`、`activation_id`、`status`、`driver_mode`、`result` 和 `exit`，随后用 `view` 检查 ProjectRuntimeState、交付链路、Timeline 与 Git 指针。这里的 State 是 SQLite 当前状态事实，不是命令侧的 ProjectRuntimeContext 对象。需要解释数据库关系或历史事实时转到 `$agentplanex-project-observe`。

开始完整介入前读取 [references/intervention.md](references/intervention.md)，确认命令前置条件、状态流与副作用。
