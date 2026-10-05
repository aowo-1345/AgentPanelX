# 测试代码架构优化 Feature Requirements

> Status: Proposed canonical baseline; awaiting explicit Plan approval.
> Source material: `.scratch/.architecture-draft/` design and roadmap documents, repository inspection at `6850d72`.
> Feature scope: reorganize and harden the test architecture; do not blindly execute the source roadmap when evidence invalidates it.

## 1. Problem and outcome

当前测试的问题不是文件数量本身，而是三个坐标混在一起：

1. 测试主要保护哪条用户可观察的不变量；
2. 测试跨过哪个执行边界；
3. 为了证明该不变量必须创建哪些资源，以及资源的生命周期和成本。

当前仓库有 35 个测试文件。`tests/conftest.py` 暴露的 `initialize_git_project` 最终调用 `tests/fixtures/git_project.py`，同时创建 Git 项目和 SQLite schema；`tests/runtime_support.py` 又提供显式的完整 Runtime composition。测试组织优化必须降低这种隐式资源耦合和证据归属不清，而不能只通过移动文件或缩小 pytest 选择范围制造表面上的速度/绿色。

目标是建立可发现、可解释、可审计、可渐进迁移的测试架构：

- primary owner、resource closure、execution policy 各自只有一个主要表达位置；
- 资源只在断言需要时创建，并且可以验证创建与关闭；
- 测试迁移、重写、参数化和清理都有证据链；
- 交付计划根据实际证据动态调整，而不是把初始 roadmap 当作不可变施工清单。

## 2. Scope and non-goals

### 2.1 In scope

- `tests/support` 原子 builders、fakes、入口 harness 和资源生命周期适配器；
- 七个平直的测试 primary-owner 入口：`infrastructure/`、`owner/`、`runtime/`、`workspace/`、`web/`、`cli/`、`live/`；
- SQLite、Git、Owner/model、Stage、Feature Runtime、Workspace、Web/CLI 和 Live 资源闭包；
- 测试价值账本、old→new nodeid 映射和可审计 cleanup；
- marker 与命令选择的盘点和必要调整；
- 迁移后的行为、覆盖、成本、路径和生命周期验收；
- 新增测试规则和未决审查清单。

### 2.2 Non-goals unless separately approved

- 不修改生产行为、Runtime 控制面、SQLite schema、Git refs、默认 Runtime 权限或真实凭证策略；
- 不修改 `.agentplanex/` Runtime 状态；
- 不因文件长度、setup 行数、assert 数量或名称相似而删除测试；
- 不通过缩小 `testpaths`、增加 skip 或改变默认 Live 选择隐藏覆盖；
- 不建立 `unit/integration/e2e/live × owner` 二维目录；
- 不一次性建立七套万能 `conftest.py`；
- 不假设 Scratch 文档中的历史 collection 数字仍然是当前事实。

如果实现过程中证明必须修改生产 seam、schema、Git refs、权限、凭证或默认执行策略，工作必须暂停并返回 Owner/用户决策，而不是扩大范围继续实现。

## 3. Definitions and invariants

- **Primary owner**：一个测试主要保护的业务、能力或入口不变量；每个测试恰有一个 primary owner。
- **Resource closure**：断言真正需要的最小资源集合及其创建、使用和关闭责任。
- **Execution policy**：offline、`e2e`、`live_model`、`slow` 等运行选择；它不重复表达业务 owner。
- **Feature runtime**：明确请求的单 Feature 复合资源，通常包含 Git、SQLite、fake Owner/Stage 和 Runtime graph。
- **Lazy workspace**：声明 Workspace 不等于创建多个完整 Runtime；只有访问指定 Feature 时才组装其 Runtime graph。
- **Delete-candidate**：尚未得到用户批准、但经过账本审查后可能需要清理的测试。

## 4. Acceptance requirements

### R-1 Baseline and ledger

M1 必须重新记录执行时的 HEAD、工作区状态、pytest collection、默认离线选择、完整离线选择、各 owner 选择、marker 使用和已知失败。Scratch 中历史的 `254/255` 只作为待核对线索，不作为当前验收事实。

账本覆盖所有实际 collected nodeid，每行至少包含：

- nodeid；
- primary invariant；
- primary owner；
- execution boundary/marker；
- 最小 resource closure；
- 粗略成本；
- 用户可观察证据或批准的契约；
- 独立失败模式；
- disposition：`keep`、`rewrite`、`merge/parametrize`、`delete-candidate` 或 `defer`；
- 理由、替代证据和迁移后的 nodeid（如适用）。

### R-2 Ownership

目标目录只表达 primary owner：

```text
tests/
├── infrastructure/
├── owner/
├── runtime/
├── workspace/
├── web/
├── cli/
└── live/
```

混合文件必须按具体不变量/断言拆分，不能按文件名整体搬迁。目录不是生产分层，也不是 Milestone 列表。

### R-3 Minimal resource closure

- SQLite 测试不得因为根 fixture 自动创建 Git；
- Git 测试不得因为根 fixture 自动创建 SQLite；
- 纯 Owner loop 不得自动创建完整项目；
- `feature_runtime` 只能在明确的单 Feature 或跨边界断言中显式请求；
- Workspace 默认只创建 Registry、binding 和 lazy factory；
- 根 `tests/conftest.py` 不得新增重资源 `autouse`；
- 可变资源默认 function scope；子进程、HTTP server、Gateway 和临时文件必须可关闭；
- 新测试资源路径必须位于 pytest 临时目录，不得向仓库 `.agentplanex/tests` 写入。

### R-4 Execution policy

默认离线测试不得调用真实模型、网络、凭证或外部进程。Live 资源必须显式选择，不能进入公共 fixture 图。现有 `unit`、`integration`、`project_owner_agent` marker 在 M1 先盘点，是否保留、别名、重命名或删除必须有证据和明确决策。

### R-5 Coverage and behavior conservation

迁移前后保存默认离线 collection、完整离线 collection、owner 组选择和成本快照。任何有意拆分、参数化、合并、删除或改变选择语义的 nodeid 都必须回到账本。不得用缩小选择集合制造“更快”或“更绿”。

### R-6 Cleanup threshold

只有同时证明以下三点，测试才可以进入用户批准的 cleanup diff：

1. 没有独立业务不变量或批准的架构/协议契约；
2. 没有独立失败模式；
3. 已有等价证据覆盖同一行为。

Candidate revision 链必须先按 worktree/SHA、SQLite schema、Owner/Tool contract、CLI 和 Delivery receipt 等不同失败边界审计，不能按文件长度批量删除。

### R-7 Environment

所有 Executor、Reviewer、Planner 和验证命令统一使用已有环境：

```text
/media/lenovo/data2/cja/AgentPlaneX-public-export/.venv
```

优先直接使用该环境中的 `python`、`pytest` 和相关工具。禁止在 Git worktree 内创建、重建或隐式生成 `.venv`。如果 CI 无法使用这个绝对路径，必须作为单独环境决策处理，不能静默替换。

### R-8 Adaptive ownership

本 Feature 的 Owner 有责任根据 Git、测试、collection、资源成本和 Reviewer 证据动态调整剩余计划。初始 Specs 和 roadmap 是批准后的工作基线，但不是无条件执行清单：

- 仅实现问题：修订 Candidate；
- Stage/Milestone 拆分不合理：咨询 Task Distributor 并更新剩余 Milestones；
- 架构假设被证伪：咨询 Planner，修改 Specs 并重新请求 Plan approval；
- 涉及生产代码、批量删除或其他重大用户取舍：暂停并请求用户决策。

每个有实质迁移的 Milestone 必须包含一个有明确目的的 hardening/验证阶段，不能以“代码移动完成”作为充分验收。

## 5. Initial decision history

- **Accepted — 执行环境**：后续工作使用 `/media/lenovo/data2/cja/AgentPlaneX-public-export/.venv`，不在 Git worktree 中重建 VENV。依据：用户明确决定；影响：requirements、architecture、roadmap 及所有 Agent contract。
- **Accepted — Owner 角色**：Owner 负责综合 Planner、Task Distributor、Reviewer、Executor 和 Git/Runtime 证据，拥有在批准范围内重排、返工、拒绝和调整交付的自主权。依据：用户明确要求；影响：R-8、roadmap 自适应规则。
- **Pending — 生产范围**：是否始终严格排除生产代码，还是允许为测试 seam 增加最小生产接口？默认严格排除；若需要则暂停请求决定。
- **Pending — 清理权限**：delete-candidate 是否逐条批准，还是允许用户批准一批具有相同证据标准的 cleanup？默认逐条保留审计证据。
- **Pending — marker 策略**：现有 `unit`、`integration`、`project_owner_agent` 是否保留或重命名？M1 先盘点，M2 再基于使用证据决定。
- **Pending — owner 边界**：混合测试按断言拆分并指定唯一 owner；个别跨边界测试是否允许记录辅助 owner 仍需在具体账本中判断，但不改变唯一 primary owner。
- **Pending — baseline 变化**：当前 HEAD 若在迁移期间变化，是否接受新 HEAD 作为新快照？默认必须重新记录，不得沿用旧 collection。
- **Pending — Live/CI 环境**：Live smoke 的凭证、外部进程和 CI 环境策略不纳入默认离线交付，需在实际需要时单独决策。
- **Pending — Plan approval**：本文件和另外两份 Spec 当前仍是 Proposed，用户批准前不得启动正式 Milestone。

## 6. Quality decision policy

Owner 在每个 Candidate 后必须回答：

1. 这个改动是否解决了原始测试架构问题，而不只是改变文件位置？
2. 资源边界是否可观察、可关闭、可重复验证？
3. collection、nodeid、断言强度和独立失败模式是否守恒？
4. 是否出现新的耦合、隐式 fixture 或维护成本？
5. 当前架构假设是否仍成立？
6. 继续执行的收益是否大于风险？
7. 是继续、revise、reject、更新 Milestone View，还是修改 Specs 并重新审批？

质量不足时必须优先保留证据并停止扩张，而不是为了满足原始日期或目录数量继续推进。
