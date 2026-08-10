# AgentPanelX

<p align="center">
  <img src="frontend/public/agentpanelx-mark.svg" alt="AgentPanelX" width="76" />
</p>

<h3 align="center">Autonomous Harness Orchestrator</h3>

<p align="center">
  面向长周期 Coding Projects 的本地优先控制平面。<br />
  Project Owner 代理用户维护目标、滚动规划并协调 Coding Agent，将交付历史沉淀为 Harness Evolution 的证据。
</p>

<p align="center">
  <a href="https://aowo-1345.github.io/AgentPanelX/"><strong>官方网站</strong></a>
  ·
  <a href="https://aowo-1345.github.io/AgentPanelX/showcase"><strong>体验 Console</strong></a>
  ·
  <a href="docs/architecture.md">系统架构</a>
  ·
  <a href="README.md">English</a>
</p>

<p align="center">
  <a href="https://github.com/aowo-1345/AgentPanelX/actions/workflows/ci.yml"><img src="https://github.com/aowo-1345/AgentPanelX/actions/workflows/ci.yml/badge.svg" alt="CI" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-7c3aed" alt="MIT License" /></a>
  <img src="https://img.shields.io/badge/Python-3.12-3776ab" alt="Python 3.12" />
  <img src="https://img.shields.io/badge/local--first-Project_Runtime-111827" alt="Local-first" />
</p>

## Agent-native 上手方式

如果你已经在使用 Codex 或 Claude Code，可以直接让 Coding Agent 帮你安装、验证并讲解 AgentPanelX：

```text
请在本地安装 AgentPanelX，并验证 Web Console 能够正常启动。然后向我解释
Project Owner、Project Runtime、Git worktree，以及 Observe / Control / Attribution
三个 Skill 如何协同工作。以仓库文档作为事实来源；如果缺少依赖，请明确报告，
不要猜测或跳过验证。
```

在 Codex 或 Claude Code 中打开这个仓库，发送上面的请求，并让 Agent 始终在仓库根目录工作。不使用 Coding Agent 时，也可以按照[快速开始](#快速开始)手动安装。

![AgentPanelX Web Console](docs/assets/showcase/board.png)

## AgentPanelX 做什么

Coding Agent 已经擅长完成单次实现任务。长周期项目仍然需要有人持续维护目标、审查规划、隔离并发工作、恢复中断现场，并决定下一步如何推进。

AgentPanelX 将这部分协调工作建模为持久化的 Project Runtime：

| 核心能力 | 作用 |
| --- | --- |
| **Project Owner 滚动交付** | 维护用户意图，跨越 Plan、Milestone 与 Stage 协调 Planner、Reviewer 和 Coding Agent。 |
| **隔离执行** | 每个 Feature 使用独立 Git branch 与 worktree，多个 Coding Agent 不共享工作目录。 |
| **可观测 Runtime** | 在同一 Web Console 展示对话、推理、Tool 输入输出、审批、Git、Plan 与 Timeline。 |
| **恢复与 Harness Evolution** | 保留 BLOCKED 证据，恢复失败上下文，并将反复出现的交付缺口沉淀为结构化 Proposal。 |

## 滚动交付循环

```mermaid
flowchart LR
    Intent[用户意图] --> Owner[Project Owner]
    Owner --> Plan[滚动规划]
    Plan --> Review[Plan / Milestone 审查]
    Review --> Stage[Stage Delivery]
    Stage --> Agent[Worktree 中的 Coding Agent]
    Agent --> Evidence[Git + Runtime 证据]
    Evidence -->|继续交付| Owner
    Evidence -->|进入阻塞| Recovery[Observe / Attribution]
    Recovery --> Evolution[Harness Evolution Proposal]
    Recovery --> Owner
```

Project Owner 将已批准的路线图与当前交付证据保留在同一上下文中，把人工参与集中到真正需要判断的高杠杆节点。

## Agent-native 操作面

Web Console 面向人，三个仓库级 Skill 则把同一个 Project Runtime 暴露给 Codex 和其他兼容的 Coding Agent：

| Skill | 能力 | 边界 |
| --- | --- | --- |
| [Observe](.codex/skills/agentplanex-project-observe/SKILL.md) | 恢复 Runtime、Plan、Git、Milestone、Stage 与 Timeline 事实。 | 只读；不审批，也不推进执行。 |
| [Control](.codex/skills/agentplanex-project-control/SKILL.md) | 发送消息、批准或拒绝 Plan、启动 Delivery，并执行授权范围内的 Runtime 动作。 | 只经过真实 Runtime；不直接修改 SQLite 或 Git ref。 |
| [Attribution](.codex/skills/agentplanex-project-attribution/SKILL.md) | 恢复 BLOCKED 检查点，fork 只读 Historical Project Owner，通过质询与反思形成 Harness Evolution Proposal。 | 只读归因；不直接解除阻塞。 |

完整工作流和权限模型见 [Agent-native 操作说明](docs/skills.md)。

## 快速开始

### 环境要求

- Python 3.12
- [`uv`](https://docs.astral.sh/uv/)
- Node.js 与 npm
- Git
- Linux 上的 Bubblewrap（`bwrap`）
- 真实交付时至少需要一个受支持的 CLI Coding Agent

### 安装并启动

```bash
git clone https://github.com/aowo-1345/AgentPanelX.git
cd AgentPanelX

uv sync
cd frontend && npm ci && npm run build && cd ..

uv run agentplanex-web
```

打开 `http://127.0.0.1:13475`。

FastAPI 在同一个端口提供 React 页面和同源 `/api`。只有真实 Project Owner Activation 需要模型凭据；凭据从 [`config/settings.yaml`](config/settings.yaml) 声明的环境变量读取。

## 架构

```text
React Web Console
      │ same-origin /api + silent polling
FastAPI Workspace API
      │
WorkspaceService ── WorkspaceWorker
      │
Feature ProjectRuntime
      ├── Project Owner / Planning / Delivery
      ├── EventBus ── Timeline projection
      ├── Git worktree / Plan commits / Candidate refs
      └── project-local SQLite / Messages / Snapshots / Stage runs
```

每个 Feature 绑定一个 managed worktree 和项目本地 SQLite Runtime。Git 与文件系统副作用和业务决策保持分层；Plan 与 Milestone Gate 校验精确 subject；EventBus 记录执行事实，但不成为第二份状态来源。

组件边界、消息时序、交付 Contract、轮询投影与 BLOCKED 归因路径见[系统架构](docs/architecture.md)。

## 开发验证

```bash
uv run pytest
uv run ruff check .
uv run mypy

cd frontend
npm run check
npm run lint
npm run build
```

默认测试不会访问模型网关。仓库协作约定和 Project Owner Tool 调试方式见[贡献指南](CONTRIBUTING.md)。

## 文档

- [系统架构](docs/architecture.md)
- [Agent-native 操作说明](docs/skills.md)
- [Console 使用说明](docs/showcase.md)
- [贡献指南](CONTRIBUTING.md)
- [安全说明](SECURITY.md)
- [MIT License](LICENSE)
