---
name: agentplanex-project-control
description: 通过标准项目视图检查 AgentPlaneX 工作区或 Triage，并经由现有运行时权限发送消息或执行 approve、reject、start 操作。当需要观察、验证或介入 AgentPlaneX 项目交付时使用；不要用于持续轮询或直接修改数据库。
---

# AgentPlaneX 项目控制

使用 AgentPlaneX 同步命令行作为控制入口，并在目标 Git 项目根目录执行命令。

## 检查项目

先读取标准视图，不要持续轮询。

```bash
uv run agentplanex workspace inspect
uv run agentplanex workspace inspect <triage-id>
```

第一条命令列出所有 Triage。第二条命令返回面向 AgentPanel 的 `TriageView`，其中包含 Triage
生命周期与待处理动作、当前规划/快照与里程碑、最近的执行/审查、交付历史、完整性警告，
以及明确推导出的 `accepted_delivery_mainline`。

将 `accepted_delivery_mainline` 视为实际交付结果。快照中的里程碑状态只是 Project Owner 当前发布的
控制视图，不能单独证明代码已经被 Git 接受。在 V2 Triage 中，配置的接受分支引用是该 Triage 自己的
分支，不是共享 `main`。

## 与 Project Owner 交互

只执行用户已经要求或授权的动作。每条命令同步返回带类型的 JSON 结果；需要最新状态时，在动作完成后
再次执行 inspect。

```bash
uv run agentplanex workspace interact <triage-id> send_message "<message>"
uv run agentplanex workspace interact <triage-id> approve
uv run agentplanex workspace interact <triage-id> reject
uv run agentplanex workspace interact <triage-id> start
```

只有存在待处理的规划审批时才能执行 `approve` 或 `reject`。只有已经发布里程碑视图并停在启动关卡时
才能执行 `start`。不要通过直接修改注册表、项目数据库、Git 引用或证据文件来伪造这些操作。

## 深入调查

标准视图不足以回答问题时，读取
[references/runtime-data.md](references/runtime-data.md)，然后针对当前问题组合只读的 `sqlite3`、
`git` 或文件查询。不要新增通用查询封装，不要假设 Git 提交或证据文件是数据库表，也不要把原始运行
轨迹当作常规检查视图。
