---
name: agentplanex-project-attribution
description: 对 AgentPlaneX 项目进入 BLOCKED 的历史执行过程进行只读归因，恢复当时的 Project Owner、Plan、Message Store、Milestone Snapshot 与 Delivery 上下文，并通过反思和追问形成统一的归因与优化 Proposal。当用户要求解释、分析或复盘项目为什么进入 BLOCKED/broken 状态，质询 Historical Owner，检查 Planner、Reviewer、Executor、Owner 与 Runtime 的协作问题，或为此提出整体优化方案时使用；不要用于解除阻塞、修改 Runtime 或一般状态观测。
---

# AgentPlaneX 项目归因

把一次 `BLOCKED` 当作需要恢复和讨论的历史过程，不把最后一条 `reason` 当作根因。先使用 `$agentplanex-project-observe` 恢复事实，再恢复 Historical Project Owner 进行反思；将两者归并成一份供用户审阅的 Proposal。

## 保持只读边界

- 只读取目标项目的 Timeline、SQLite、Git、Message Store 和 Agent artifact。
- 不调用 Project Control，不修改 Runtime、SQLite、正式 Message History、工作树或 Git ref。
- 不从 Historical Owner Fork 执行 Tool；只在 Fork 内保留调查对话。
- 不把归因自动延伸为解除阻塞、修复代码或实施优化。用户确认 Proposal 后再进入其他工作流。
- 不预设单一责任方、固定失败分类或每一种 `reason` 的独立归因流程。

## Workflow

### 1. 定位 BLOCKED 检查点

取得用户指定的目标项目和 BLOCKED Event。未指定 Event 时，使用 `$agentplanex-project-observe` 定位相关 `RUNTIME_CONTEXT_UPDATED` 状态迁移；如果存在多个候选且无法从请求判断目标，再让用户选择。

记录 `event_id`、`triage_id`、`reason`、时间以及可用的 `react_loop_id`、`message_id` 和业务对象标识。把 `reason` 仅视为触发状态迁移的直接原因，继续调查为什么流程会走到这里。

### 2. 使用 Observe 恢复归因上下文

调用 `$agentplanex-project-observe`，从 Event 沿现有对象关系恢复本次调查需要的最小上下文：

```text
BLOCKED Event
├── react_loop_id / message_id
│   └── Owner Activation
│       └── Summary + Message Tail + Tool 交互
├── 触发阻塞的业务对象
│   ├── Plan / Hard Gate
│   └── Run / StageRun / Candidate
├── StageRun.snapshot_id
│   └── Milestone Snapshot
│       └── plan_commit_sha
│           └── 当时实际采用的 Plan
└── run_id / stage_run_id
    └── 有序 StageRun
        └── 输入、结果、失败、Commit、Candidate 与审查结果
```

按实际执行阶段处理不存在的对象：

- 已进入 Delivery 时，从 StageRun 找到固定 Snapshot 和 Plan，并还原该 Run 已发生的 Stage 与 Candidate 历史。
- 在 Plan、Hard Gate 或审批阶段阻塞时，恢复当时的 Plan、审查和 Owner 上下文；不要虚构尚未产生的 Snapshot 或 Delivery。

把结果整理成当前调查使用的只读上下文，包括阻塞事件、Owner 对话检查点、规划基线、Snapshot 以及已经发生的 Delivery。不要新增中间持久化对象，也不要重复实现 Observe 的 SQLite 或 Git 查询规则。

### 3. 恢复 Historical Project Owner

如果当前 Invocation 明确提供 `owner_fork_command`，它是本次运行的精确入口；在其后追加
下面的 `--message-id`、可选 `--summary-id` 与 `--print-context` 参数，不要自行改回仓库脚本。
以下脚本形式用于源码仓库中的人工调查。

先通过 Runtime 的共享只读 `ProjectOwnerContextQuery.latest_summary_id_through()`
解析不晚于 BLOCKED `message_id` 的最近 Summary watermark。不要直接查询
`summary_history` SQL，也不要读取 `project_owner_agent.summary_id` 猜测历史版本。
如果查询返回 Summary ID，把它作为下面的显式选择；如果返回 `None`，省略
`--summary-id` 并恢复完整 raw history。

先检查将要恢复的 Owner 上下文：

```bash
uv run python scripts/debug_owner_fork_cli.py \
  --cwd <目标项目> \
  --message-id <BLOCKED 检查点消息> \
  --summary-id <当时固定的 Summary，可选> \
  --print-context
```

确认消息检查点、Summary 选择和上下文范围后，去掉 `--print-context` 打开只读 Historical Owner Fork。不要把用户当前的归因请求写入正式 Message History；把它作为 Fork 中的新调查消息。

### 4. 先让 Owner 完整反思

根据已恢复的上下文生成一条通用反思指令。以以下内容为基线，并按本次项目补充必要对象名称：

```text
项目在这个历史检查点进入了 BLOCKED。

请结合你当时实际看到的用户消息、Plan、Roadmap、Milestone、Stage、
其他 Agent 的建议、Tool Result 和执行结果，回顾项目从规划到进入
BLOCKED 的过程，并反思：

- 当前目标仓库有哪些实际问题；
- Planner、Reviewer、Executor、Project Owner 与 Runtime 之间的规划、
  建议、输入、判断或交接有哪些不合理之处；
- 必要的信息是否进入正式产物并传递给后续角色；
- 哪些问题没有被较早发现，最后共同造成了这次阻塞；
- 目标仓库和 AgentPlaneX 下一轮分别可以改善什么。

不要为自己辩护，不要强行寻找单一责任方。只使用这个检查点之前
已经存在的信息，明确区分事实、判断和仍不确定的问题。
```

让 Owner 先完成一轮整体反思，不要一开始就用固定问题清单逐项审讯。

### 5. 根据反思继续追问

从 Owner 的反思中选择会影响归因结论的具体判断，按需再次使用 `$agentplanex-project-observe` 查看对应 Plan、Hard Gate、Snapshot、StageRun、Message、artifact、commit 或 ref，再围绕实际疑点自然追问 Owner。

重点理解规划、审查、上下文传递和执行如何共同形成阻塞。不要机械记录 Codex 接受、修正或否定了 Owner 的哪些解释，也不要建立单独的证据裁决表。将有依据的结论直接合并；对无法确认的内容保留不确定性。

当以下内容已经足以形成连贯说明时结束追问：

- 当时要完成什么、执行到了哪里；
- 项目怎样从规划、审查和交付走到 BLOCKED；
- 目标仓库与 AgentPlaneX 的协作流程分别暴露了什么问题；
- 哪些优化方向值得提交用户审阅。

### 6. 产出统一 Proposal

把 Codex 的观察与 Historical Owner 的反思归并成一份文档，不分别输出“Owner 证词”和“Codex 裁决”。将相关事件和对象引用写在对应结论旁边，不复述整条 Timeline。

```markdown
# BLOCKED 归因与优化 Proposal

## 归因与反思总结

### 当时的目标与执行位置

### 从规划到 BLOCKED 的过程

### 对协作过程的反思

### 归因总结

## 总体优化 Proposal

### 现状存在什么问题

### 预期达到什么效果

### 核心设计决策

## 尚未确定的问题
```

只形成一个总体优化 Proposal。在各小节内分别说明目标仓库和 AgentPlaneX 的问题及其相互作用，不拆成两份方案，不加入独立的“核心验证目标”，也不把“接下来怎样解除 BLOCKED”写成主要内容。

把 Proposal 写到用户指定的位置；未指定文件时，直接在回复中提供，不自行创建新的文档路径。
