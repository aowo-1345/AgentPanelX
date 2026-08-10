# Public Console

AgentPanelX 的 Public Console 将本地 Project Runtime 导出的项目快照发布到 GitHub Pages。它复用生产 Board、Workspace、Conversation、Tool activity 与 Side Panels 组件，让访问者可以直接浏览一次完整的项目交付现场。

## 访问入口

- 官网：<https://aowo-1345.github.io/AgentPanelX/>
- Console：<https://aowo-1345.github.io/AgentPanelX/console>
- Showcase：<https://aowo-1345.github.io/AgentPanelX/showcase>

本地启动后也可以访问：

```text
http://127.0.0.1:13475/console
```

## 两种浏览方式

### Console

Board 保留 Runtime 中的真实 Project、Feature 与状态分布。点击任意 Feature 后进入同一条记录的 Workspace，可继续展开：

- Project Owner 对话与 Activation 状态；
- Bash、Agent collaboration 等 Tool activity 的输入和输出；
- Plan 文档、Milestone 与 Stage 进度；
- Feature branch、worktree 与 Git 信息；
- Runtime Timeline 与失败现场。

Public Console 是只读入口，交互结构与本地 Console 保持一致；提交消息、Plan 决策和 Delivery 控制仍在连接本地 Project Runtime 的 Console 中执行。

### Showcase

Showcase 按章节串联一次自举交付：从用户目标、Plan approval 和 Stage delivery，推进到 BLOCKED、Observe、Attribution、Control 与 Harness Evolution Proposal。Console 右上角的 `Showcase` 可以随时进入该流程；Showcase 右上角的 `Console` 返回真实项目 Board。

## 更新公开快照

先启动本地 AgentPanelX，使 `/api/projects`、`/api/features` 与 Workspace API 可访问，然后运行：

```bash
uv run python scripts/export_console_snapshot.py \
  --base-url http://127.0.0.1:13475
```

导出器会读取 Board 及每个 Feature 的 Workspace projection，替换本机绝对路径并过滤凭据格式，写入：

```text
frontend/src/showcase/consoleSnapshot.json
```

随后执行前端验证与构建：

```bash
cd frontend
npm run check
npm run lint
npm run build
```

## 发布

GitHub Actions 构建前端并发布 GitHub Pages。`/console` 与携带 `project`、`feature` 查询参数的 Workspace 链接都由同一个 SPA 入口处理。

发布前检查：

- Board 中的 Project、Feature、状态与本地 Runtime 一致；
- 每张卡片都能进入对应 Workspace；
- Tool activity 可以展开并显示完整输入与输出；
- Plan、Milestone、Git 与 Timeline 面板可浏览；
- 页面中不包含本机绝对路径、凭据或私有 request id；
- `/` 与 `/console` 在 GitHub Pages 上可直接访问。
