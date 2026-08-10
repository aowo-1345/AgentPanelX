# Web Showcase

AgentPanelX 的公开演示采用确定性、脱敏的静态数据，目的是让访问者无需模型凭据、后端数据库或等待长时间 Stage，也能完整体验真实 Web Console 的信息结构。

## 入口

构建并启动项目后访问：

```text
http://127.0.0.1:13475/showcase
```

Showcase 不发起 `/api` 请求，不写入目标项目的 `.agentplanex`，也不伪造外部模型调用、Git commit 或测试结果。

## 两层体验

### 1. 静态 Board

默认页面同时展示六个 Kanban 状态与多个 Project / Feature：

- Triage：刚进入 Project Owner 的意图；
- Todo：等待 Owner 继续规划；
- Ready：`WAITING_APPROVAL`；
- In Progress：正在投影 Tool activity；
- Blocked：`BROKEN_STAGE` 或 Assistance 调查中；
- Done：Harness Evolution Proposal 与公开证据索引。

搜索与 Project / Status filter 均在浏览器本地工作。点击任意卡片会进入与该状态最接近的重点案例章节。

![Static Showcase Board](assets/showcase/board.png)

### 2. Self-hosting Workspace

重点案例讲述 “AgentPanelX 使用自身 Runtime 管理 Ultra Mode 展示级垂直切片”：

1. **Intent**：用户交付目标进入 Project Owner；
2. **Plan**：Planner 返回可观察 Contract，并形成 Plan approval；
3. **Delivery**：Stage 执行与 Tool activity 同时可见；
4. **Blocked**：Stage 失败形成固定 Block Incident；
5. **Ultra**：Observe 恢复权威证据，Attribution 开始调查；
6. **Evolution**：失败被归类为 Harness context handoff，并生成 Proposal；
7. **Done**：Control / Reviewer 结果与 evidence index 汇合。

每个章节复用生产 `ChatArea`、`SidePanels`、Tool activity 和 Plan document 组件；只有数据源切换为 `frontend/src/showcase/data.ts` 中的 fixture。

## 事实边界

Showcase 可以证明：

- 当前 Web Console 能稳定呈现完整的目标、计划、工具、交付、失败、归因与 Proposal 故事；
- Board 和 Workspace 组件可以在无需 API 的静态托管环境运行；
- 三个 Skill 的输入、状态和输出能够映射到同一个项目视图；
- Ultra Mode 与 Harness Evolution 的交互形态和 Artifact Contract 已经明确。

Showcase 不能替代：

- 真实模型网关成功记录；
- 不存在的 commit SHA 或测试结果；
- 自动 Assistance Worker 已经完成生产调度的证明；
- Proposal 已被自动应用并经过完整重放的证明。

## Canonical 素材

公开 README 与后续官网共用 `docs/assets/showcase/`：

| 文件 | 证明内容 |
| --- | --- |
| `board.png` | 多项目与全状态 Board，包括 waiting / blocked / done |
| `intent.png` | 用户意图进入 Project Owner |
| `plan.png` | Plan Tool 与审批证据 |
| `delivery.png` | Stage 执行与 Tool activity |
| `blocked.png` | 失败被投影为固定 BLOCKED 状态 |
| `ultra.png` | Observe / Attribution 调查 |
| `evolution.png` | 结构化 Harness Evolution Proposal |
| `done.png` | 最终 Review 与交付证据汇总 |
| `demo.webm` | 官网 → Board → BLOCKED Tool → Evolution → Done 的 53 秒录像 |

Canonical 截图为 1440×900，内容不包含真实用户路径、凭据、模型 request id 或私人仓库名。

## 验证

```bash
cd frontend
npm run check
npm run lint
npm run build
```

浏览器验收至少覆盖：

- `/showcase` 默认进入 Board；
- 1440×900 可以同时看见六列；
- `WAITING_APPROVAL` 与 `BROKEN_STAGE` 可见；
- 点击 Ultra Mode 卡片进入 `chapter=blocked`；
- Workspace 中 Tool Step 可展开；
- 点击 `Board` 返回静态 Board；
- 页面控制台没有请求 `/api` 的错误。
