# 测试代码架构优化 Feature Roadmap

> Status: Proposed canonical baseline; awaiting explicit Plan approval.
> Strategic scope: test ownership, resource closure, migration evidence, adaptive hardening and maintenance governance.
> Execution environment: use `/media/lenovo/data2/cja/AgentPlaneX-public-export/.venv`; never create a VENV inside the Git worktree.

## 1. Delivery strategy

本 Feature 采用证据驱动的滚动交付，而不是无条件执行固定目录迁移。三阶段是战略路线：

1. **M1：价值审查与基线** — 先知道每个测试保护什么、需要什么资源、当前成本和失败边界；
2. **M2：资源 seam、owner 迁移与批准清理** — 先建立可验证的资源边界，再渐进迁移；
3. **M3：最终验收与维护规则** — 证明覆盖、行为、成本和生命周期没有被迁移破坏，并固化未来规则。

`infrastructure`、`owner`、`runtime`、`workspace`、`web`、`cli`、`live` 是最终测试归属入口，不是七个 Milestone。

```mermaid
flowchart LR
    M1[M1 价值审查与基线] --> M2[M2 资源 seam、迁移与批准清理]
    M2 --> M3[M3 最终验收与维护规则]
    M1 -. 证据可能改变 .-> Replan[Owner / Planner / Distributor 重规划]
    M2 -. hardening 发现架构问题 .-> Replan
    Replan --> M2
    Replan --> M3
```

## 2. M1 — 价值审查与基线

### Outcome

得到可重跑、可审计的测试价值账本和迁移前快照；不移动、不删除测试，不修改生产代码，不改变 Runtime 状态。

### Planned work

- 使用指定已有 VENV 记录 HEAD、工作区状态、默认 collection、完整离线 collection、marker 选择、各候选 owner 选择和已知失败；
- 对每个 collected nodeid 记录 primary invariant、owner、execution boundary、最小 resource closure、成本、用户证据、失败模式和 disposition；
- 优先审查混合/膨胀文件：`test_debug_tool_cli.py`、`test_workspace_cli.py`、`test_runtime_configuration.py`、`test_project_owner_context_memory.py`、`test_agent_invocation_contracts.py`、`test_sqlite_persistence.py`、`test_project_runtime_automatic_loop.py`；
- Candidate revision 相关测试先按不同失败边界保留，不能凭文件长度清理；
- 产出 marker 建议、删除候选、延期清单、old→new 迁移前快照和需要用户决定的问题。

### Exit evidence

- 账本覆盖当前实际 collection，而不是沿用历史数字；
- 每个 delete-candidate 有三项清理门槛证据，否则是 `defer`；
- 默认/完整离线 collection 可重跑；
- 没有生产代码、Runtime、schema、Git refs 或测试删除变更；
- 所有命令使用指定已有 VENV。

### Adaptive decision

M1 结束后 Owner 不自动进入 M2。若账本显示 owner 边界、resource closure 或 marker 设计不稳定，先咨询 Planner/Task Distributor，可能：

- 增加资源或边界调查 Stage；
- 选择少量代表 owner 做试点；
- 调整 `architecture.md`；
- 收缩或重排后续迁移；
- 将重大取舍返回用户。

## 3. M2 — 资源 seam、owner 迁移与批准清理

### Outcome

测试可以按最小资源闭包选择，目标 owner 目录完成有证据的渐进迁移，任何清理都可追溯到 M1 账本和替代证据。

### Planned work

- 在 `tests/support` 建立 SQLite、Git、fake model/transport、fake stage、入口 harness 和生命周期适配器；
- 保留显式 `feature_runtime` 复合资源；
- 建立惰性 `make_workspace(...)`，声明 Workspace 不等于创建多个 Runtime；
- 保持根 `conftest.py` 轻量，不新增重资源 autouse；
- 先迁移边界清楚的 Infrastructure、Owner、Runtime、Workspace、Web、CLI、Live 用例；
- 对混合文件按具体不变量拆分；
- 只执行用户批准且满足账本门槛的 merge/parametrize/delete cleanup；
- 为所有移动、拆分和清理保留 old→new nodeid、资源边界和替代证据。

### Required hardening

M2 必须包含独立且有实质目的的 hardening 阶段，检查：

- SQLite/Git/Owner/Runtime/Workspace 资源是否真正解耦；
- 所有资源路径、后台进程、HTTP/Gateway 是否正确关闭；
- 是否出现 test-to-test import、隐式 sibling `conftest.py` 依赖或新的仓库写入；
- collection、断言强度、独立失败模式和默认选择是否守恒；
- 迁移后架构是否比迁移前更容易解释，而不是只改变目录外观。

### Adaptive decision

如果 hardening 发现原有分解不合理，Owner 可以暂停目录迁移，要求 Task Distributor 重新分解，或让 Planner 重新挑战架构。不得为了完成“七个目录”而继续扩张错误的资源 seam。

## 4. M3 — 最终验收与维护规则

### Outcome

迁移前后的覆盖、行为、资源和成本差异可解释，新增测试有明确的归属和资源规则，未完成的业务判断保留在延期清单。

### Planned work

- 对照 old/new collection 和 nodeid 映射；
- 运行默认离线回归、各 owner 目标组和完整离线集合；
- 分别测量默认、Runtime、Workspace、Web、CLI 和 Live-excluded 选择；
- 区分已有基线失败、迁移回归和环境失败；
- 检查没有重资源 root autouse、test-to-test fake import、sibling fixture 隐式依赖和新的 Runtime 仓库写入；
- 固化 contributor rule、测试审查模板和 deferred ledger。

### Maintenance rules

新增测试必须说明：

- primary invariant 和 owner；
- 最小 resource closure；
- execution policy；
- 用户可观察证据或批准的契约；
- 独立失败模式；
- 选择该 fixture/resource 的理由。

Live 和 credentialed 测试不得进入默认 pytest 入口；重复、低价值或只测私有实现形状的候选必须进入账本，不能凭感觉删除。

## 5. Rolling delivery and replanning rules

- 每个 Milestone 完成后，Owner 检查固定 Git Candidate、测试/collection 证据和 Reviewer 意见；
- `accept` 表示 Milestone 完成，随后咨询 Task Distributor 再决定是否保持或替换剩余 View；
- `revise` 表示同一 Milestone 仍未完成，要求 Executor 根据具体反馈修订；
- `reject` 表示 Candidate 不满足批准范围，停止自动推进并重新判断实现、分解或 Specs；
- 仅当剩余交付分解发生变化时调用 `update_milestones`，并提交全部尚未完成的 pending Milestones；
- 如果 requirements、architecture 或战略 roadmap 发生变化，必须先更新 canonical Specs 并重新请求 Plan approval；
- 如果只是 Stage 粒度、顺序或 hardening 需要调整，可在批准 Plan 范围内更新 Milestone View；
- Stage 失败时项目保持 BLOCKED，不绕过失败继续执行；只有批准 Plan 和 Snapshot 仍有效时才允许重试；
- 每个新的不同 Milestone 开始前重新咨询 Task Distributor；同一 Milestone 的 Stage 之间不重复咨询，除非失败证据已经推翻当前分解。

## 6. Stop conditions

以下情况停止迁移或清理，回到分析和决策：

- 主分支或 Runtime 契约变化，导致基线漂移；
- 无法解释某个测试的 primary owner 或 resource closure；
- old/new nodeid 或 collection 无法对照；
- 需要修改生产代码、schema、Git refs、权限、默认模型策略或凭证策略；
- 迁移后的速度改善只能通过隐藏覆盖获得；
- Reviewer 发现硬化阶段没有验证实质风险；
- 删除候选缺少独立失败模式和替代证据；
- 现有架构假设被实际实现证伪。

## 7. Review record

- Scratch architecture/roadmap：作为输入材料，不能替代本 Feature 的 canonical Specs。
- Planner advisory：已完成；建议已由 Owner 审查并部分采纳。
- Task Distributor：Plan approval 后用于初始 Milestone View，并在每个已接受 Milestone 后用于滚动重规划。
- Reviewer：对每个固定 Candidate 提供独立证据，最终接受/拒绝由 Owner 决定。
- 本 roadmap 当前为 Proposed，等待用户审批后才可建立正式 Milestone View。
