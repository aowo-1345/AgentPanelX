---
name: agentplanex-project-observe
description: 理解 AgentPlaneX 项目的执行上下文、交付历史与 Timeline 事实。当项目负责人、执行者、规划者、审查者、硬门控或调查智能体需要定位 Triage、解释运行时状态、检查 SQLite 与 Git 证据，或说明项目如何到达当前状态时使用。
---

# AgentPlaneX 项目观测

先读取 `requirements.md`、`architecture.md` 和 `roadmap.md`，理解长期有效的项目意图。它们是规格文档，不是运行时当前状态。

以启动时提供的 `triage_id`、角色、工作树和固定工作对象为起点。固定的 Stage、Snapshot、Plan commit 或 Candidate 不得被更新后的运行时对象悄然替换。

## 核心流程

```mermaid
sequenceDiagram
    actor User as 用户
    participant Owner as 项目负责人
    participant Runtime as 运行时服务
    participant Snapshot as Milestone Snapshot
    participant Runner as Delivery Runner
    participant Git
    participant Timeline

    User->>Owner: 提供意图或受控决策
    Owner->>Runtime: 发出受保护的 Tool Action
    Runtime->>Runtime: 校验契约并更新 Context
    Runtime->>Snapshot: 发布不可变的 Milestone View
    Runtime->>Runner: 入队下一个有序 StageRun
    Runner->>Git: 执行 Stage 并创建 Candidate commit
    Runner->>Runtime: 回报 Stage 成功、失败或 Candidate 就绪
    Runtime->>Timeline: 记录已成立的业务事实
    Runtime->>Owner: 需要决策时入队 EXECUTION_RESULT
    Owner->>Runtime: 接受或拒绝 Candidate
    Runtime->>Git: 快进接受的 Candidate 或保留拒绝的 ref
    Runtime->>Snapshot: 发布后继 Snapshot 或标记 DONE
```

`project_runtime_context` 回答项目现在在哪里。Snapshot、StageRun 和 Git 回答哪些计划与代码事实已经存在。Timeline 解释重要的历史事实；它不重建当前状态，也不负责调度工作。

在解释 Timeline payload、SQLite 关系、状态值或开展角色相关调查前，阅读 [references/detail.md](references/detail.md)。只读查询 SQLite 和 Git。所有状态变化必须经过运行时的 Tool 或受控命令，禁止直接编辑 SQLite、Git ref 或证据文件。
