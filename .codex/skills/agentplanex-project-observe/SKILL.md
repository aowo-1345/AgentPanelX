---
name: agentplanex-project-observe
description: 理解 AgentPlaneX 项目的执行上下文、交付历史与 Timeline 事实。当项目负责人、执行者、规划者、审查者、硬门控或调查智能体需要定位 Triage、解释运行时状态、检查 SQLite 与 Git 证据、核对 Plan/Hard Gate artifact 与 Stage/Candidate commit/ref，或说明项目如何到达当前状态时使用。
---

# AgentPlaneX 项目观测

先读取 `requirements.md`、`architecture.md` 和 `roadmap.md`，理解长期有效的项目意图。它们是规格文档，不是运行时当前状态。

以启动时提供的 `triage_id`、角色、工作树和固定工作对象为起点。固定的 Stage、Snapshot、Plan commit 或 Candidate 不得被更新后的运行时对象悄然替换。

始终成对审计 SQLite 与 Git：SQLite 证明控制面状态、对象关系和已记录的业务事实；Git 证明 Spec、代码、交付文档、commit 可达性与 ref 身份。`.agentplanex/agent-workspaces/` 中的 Agent artifact 是由 SQLite artifact descriptor 锚定的辅助证据，不是第三个独立状态权威。

## 共享工作流与角色边界

三份 Spec 合起来是规范性的 Project Plan。`requirements.md` 记录用户目标、范围和验收标准，`architecture.md` 记录系统边界和长期设计约束，`roadmap.md` 记录总体交付策略。Planner 的 `documents/plan.md` 是可被 Owner 采纳的建议；Reviewer 或 Hard Gate 的 `documents/review.md` 是固定对象的审查证据。两者都不会自动改写 Spec、Runtime 或接受分支。

`TODO` 用于与用户维护 Spec、请求 Plan 批准和建立初始完整 Milestone View，不自动调用 Hard Gate。首次 Run 获用户批准后进入 `IN_PROGRESS`；此后提交新的 Plan 或完整 Milestone View 时，Runtime 才自动调用相应 Hard Gate。`BLOCKED` 用于 Owner 处理终态失败，不调用 Hard Gate；若批准的 Plan 和 Snapshot 仍有效，可以重新运行第一个未完成 Milestone。

各角色共享同一套事实，但职责不同：

| 角色 | 负责 | 不负责 |
|---|---|---|
| Project Owner | 维护用户意图和三份 Spec，采纳或拒绝 Agent 建议，发布完整 Milestone View，对 Candidate 作最终决定 | 冒充 Planner、Reviewer、Hard Gate 或 Executor，自行替用户批准 Plan |
| Planner | 针对委派问题讨论或产出 `documents/plan.md` | 直接修改 canonical Spec、发布 Milestones 或批准 Plan |
| Reviewer | 审查固定 Plan、Milestone 或 Candidate，并产出可追溯证据 | 接受 Candidate、改变 Runtime 或替 Owner 决策 |
| Hard Gate | 在 Runtime 指定的 `IN_PROGRESS` 受保护操作中判断固定 subject 是否可继续 | 在 `TODO`/`BLOCKED` 自行运行、修改 subject 或决定用户意图 |
| Stage Executor | 在固定 worktree 中实现固定 Stage 并留下 delivery document | 修改三份 canonical Spec、重规划 Milestone、提交或接受 Candidate |

## 核心流程

```mermaid
sequenceDiagram
    actor User as 用户
    participant Owner as 项目负责人
    participant Runtime as 运行时服务
    participant SQLite as SQLite 状态与 Timeline
    participant Runner as Delivery Runner
    participant Git as Git commits 与 refs

    User->>Owner: 提供意图或受控决策
    Owner->>Git: 维护三份 canonical Spec
    Owner->>Runtime: 请求用户批准精确 Plan
    Runtime->>Git: 仅在用户批准后提交 Spec baseline
    Owner->>Runtime: 发布完整 Milestone View
    Runtime->>SQLite: 发布不可变 Snapshot
    User->>Runtime: 批准首次 Run
    Runtime->>Runner: 入队下一个有序 StageRun
    Runner->>Git: 从固定输入执行并创建 Stage commit/ref
    Runner->>Runtime: 回报 Stage 成功、失败或 Candidate 就绪
    Runtime->>SQLite: 固化 StageRun、Context 与 Timeline 事实
    Runtime->>Owner: 需要决策时入队 EXECUTION_RESULT
    Owner->>Runtime: 接受或拒绝 Candidate
    Runtime->>Git: 快进接受分支；Candidate ref 保持可审计
    Runtime->>SQLite: 发布后继 Snapshot 或标记 DONE
```

`project_runtime_state` 回答项目现在在哪里。Snapshot、StageRun 和 Git 回答哪些计划与代码事实已经存在。Timeline 解释重要的历史事实；它不重建当前状态，也不负责调度工作。

## 核心证据产出时序

使用下图回答“某项证据何时产生、保存在哪里、如何与另一侧交叉验证”。图中的“接受分支”通常是 `main`：Plan 审批时先以项目目标 Worktree 当前附着的分支为准，Rolling Delivery 开始后再以 `project_runtime_state.git_branch` 固定。

先用下面的文件树建立空间直觉。它表示“Plan 已批准、已有 Agent 审计 artifact、当前 Run 正在执行一个 Stage”时可能出现的中途切片；`{...}` 是示意 ID，不保证每个目录在所有状态下都存在。

```text
<project>/                                      # 目标 Git Worktree，附着在接受分支
├── architecture.md                             # 当前 Worktree 的 Spec
├── requirements.md
├── roadmap.md
├── src/ ...                                    # 接受分支当前代码
├── docs/agentplanex/deliveries/
│   └── {accepted_run_id}/ ...                  # 只包含已经进入接受分支的历史交付文档
└── .agentplanex/                                # Runtime-owned；其中 DB/Agent artifact 不进入目标分支
    ├── agentplanex.sqlite3                     # Context / Snapshot / StageRun / Timeline
    ├── agent-workspaces/
    │   ├── {planner_workspace_id}/             # Planner 的持久 workspace
    │   │   ├── workspace.json
    │   │   ├── documents/plan.md               # Planner Task artifact；不自动进入接受分支
    │   │   └── outbox/{invocation_id}/result.json
    │   └── {reviewer_workspace_id}/            # Reviewer/Hard Gate 的隔离 workspace
    │       ├── workspace.json
    │       ├── inputs/milestones.json          # 仅 Milestone Gate 需要时出现
    │       ├── documents/review.md              # Gate 审计 artifact；不进入接受分支
    │       └── outbox/{invocation_id}/result.json
    └── delivery-worktrees/
        └── {active_run_id}/                     # 从 StageRun.input_commit_sha 建立的 detached worktree
            ├── .git                            # 指向主仓库的 linked-worktree 元数据
            ├── architecture.md                  # 固定输入 commit 中的完整项目树
            ├── requirements.md
            ├── roadmap.md
            ├── src/ ...                        # 当前 Stage 正在产生的代码变更
            └── docs/agentplanex/deliveries/
                └── {active_run_id}/
                    └── {stage_key}.md           # Stage Contract 要求的交付文档

Git 逻辑对象与 refs（不要依赖 `.git/refs` 的物理文件布局）：

接受分支（通常 main） ----------------------> {git_main_version}
refs/agentplanex/runs/{active_run_id} --------> {latest_successful_stage_commit}  # 已有成功 Stage 时
refs/agentplanex/candidates/{active_run_id} ---> {final_stage_commit}              # 最终 Stage 完成后
```

区分这两个 Worktree：目标 Worktree 在 Candidate 决策前仍停留于 `git_main_version`；当前 Run 的未接受代码和交付文档先出现在 detached Delivery Worktree，成功后进入 Stage commit/ref，只有 Candidate 被接受后才可从接受分支看到。Candidate 就绪或 Stage 失败后，Delivery Worktree 可以被清理，因此不要把它当成持久审计证据。

```mermaid
sequenceDiagram
    actor User as 用户
    participant Owner as Project Owner
    participant Runtime as Runtime / Services
    participant Artifacts as Agent Workspaces<br/>Plan / Review
    participant Delivery as Delivery Worktree<br/>detached
    participant Git as Git commits / refs
    participant DB as SQLite<br/>Context / Snapshot / StageRun / Timeline

    Note over Owner,DB: Plan 与 Milestone 证据
    Owner->>Artifacts: 可选委派 Planner
    Artifacts-->>Owner: plan.md + artifact descriptor
    Owner->>Runtime: 请求批准精确三份 Spec
    alt TODO 或 BLOCKED
        Runtime->>DB: 不运行 Hard Gate，记录待批准 Spec 摘要
    else IN_PROGRESS
        Runtime->>Artifacts: 执行固定 Plan Hard Gate Contract
        Runtime->>DB: 记录 review descriptor 与待批准 Spec 摘要
    end
    User->>Runtime: 批准 Plan
    Runtime->>Git: 创建 Plan commit
    Runtime->>DB: 关联 plan_commit_sha
    Owner->>Runtime: 发布完整 Milestone View
    alt TODO 或 BLOCKED
        Runtime->>DB: 不运行 Hard Gate，记录不可变 Snapshot
    else IN_PROGRESS
        Runtime->>Artifacts: 执行固定 Milestone Hard Gate Contract
        Runtime->>DB: 记录 review descriptor 与不可变 Snapshot
    end

    Note over Owner,DB: Stage 与 Candidate 证据
    Runtime->>DB: 固定 Snapshot、Stage 与 input_commit_sha
    Runtime->>Delivery: 执行 Stage Contract
    Delivery-->>Runtime: 代码 + delivery document
    Runtime->>Git: 创建 Stage commit 与 Run/Candidate refs
    Runtime->>DB: 记录 output/failure，并唤醒 Owner
    Owner->>Runtime: 接受或拒绝 Candidate
    alt accept
        Runtime->>Git: fast-forward 接受分支
        Runtime->>DB: 发布后继 Snapshot 与 ACCEPTED/DONE 事实
    else reject
        Runtime->>DB: 记录 REJECTED；Git ref 保留 Candidate
    end
```

按以下边界解释图中的产物：

| 证据位置 | 会出现什么 | 不应推断什么 |
|---|---|---|
| 项目 Worktree | 审批前可变的三份 Spec，以及当前接受分支检出的文件 | 未提交 Spec 不是已批准 Plan；工作树当前内容不能替代历史 commit |
| `.agentplanex/agent-workspaces/<workspace>/` | Planner 的 `documents/plan.md`、Hard Gate 的 `documents/review.md`、每次 Task/Gate 的 `outbox/<invocation>/result.json` | 该目录被 Git 排除；不要声称这些文档位于 `main`，也不要仅凭文件存在证明 Gate 结果 |
| 接受分支历史 | 通过用户批准提交的三份 Spec；接受 Candidate 后的代码和每个 Stage delivery document | Planner `plan.md`、Hard Gate `review.md` 和被拒 Candidate 不会因此进入接受分支 |
| `refs/agentplanex/runs/<run_id>` | 某次 Run 最新成功 Stage 的 commit | 该 ref 不表示 Candidate 已被接受 |
| `refs/agentplanex/candidates/<run_id>` | 最终 Stage Candidate；接受或拒绝后仍保持 Git 可达性 | ref 存在不表示 Candidate 位于接受分支；必须检查决策 Timeline 和分支可达性 |
| SQLite | 当前 Context、不可变 Snapshot、StageRun 输入/输出、Activation/Message、Timeline 及 artifact descriptor | 当前指针为空不表示历史对象不存在；Timeline 也不能替代 Git 对象或当前状态表 |

核心审计只要求闭合三条证据链：

| 审计对象 | 最小闭环 |
|---|---|
| Plan 批准 | `subject_digest` → 用户决定 → `plan_commit_sha` |
| Rolling Hard Gate | `subject_digest` → `review.md` descriptor → 受保护操作结果 |
| Stage / Delivery | Snapshot 与 `input_commit_sha` → Stage commit/ref → 代码 diff 与 delivery document |
| Candidate 决策 | Candidate ref → 接受/拒绝事实 → 接受分支可达性与后继 Snapshot |

先用 Context 定位“现在”，再用不可变 SQLite 行和 Git 对象解释“如何到达”。任何单个当前指针、Timeline 事件或 workspace 文件都不足以独立完成归因；若 Timeline 缺失，明确记录证据缺口，不要据此推翻已经成立的 SQLite 终态或 Git 事实。

正常协作先使用本页的流程和角色边界。需要解释 Timeline payload、SQLite 关系、状态值或历史对象时，再阅读 [references/detail.md](references/detail.md)。只读查询 SQLite 和 Git。所有状态变化必须经过运行时的 Tool 或受控命令，禁止直接编辑 SQLite、Git ref 或证据文件。
