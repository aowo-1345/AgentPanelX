# Contributing to AgentPanelX

感谢参与 AgentPanelX。当前项目处于 `0.1.x`，优先接受能够简化 Runtime、增强可观察性或补齐真实端到端行为的改动。

## 开发环境

```bash
uv sync

cd frontend
npm install
```

Python 版本和依赖以 `pyproject.toml` 为准，使用 `uv` 运行 Python 命令。

## 开发原则

- 优先简单、直接、可观察的设计，不为尚无调用方的能力预建框架。
- Git、文件系统、外部进程等副作用与业务决策保持清晰边界。
- 新增 `src/` 能力前，先定义用户能够直接观察的真实行为与验证方式。
- 不使用直接 SQLite 写入或手工 Git ref 变化绕过 Project Runtime。
- 不提交模型凭据、真实用户 Runtime、数据库、trajectory、transcript 或临时 worktree。
- Showcase 数据必须脱敏，并明确标识为 deterministic fixture。

## 验证

后端：

```bash
uv run pytest
uv run ruff check .
uv run mypy
```

前端：

```bash
cd frontend
npm run check
npm run lint
npm run build
```

默认测试不得访问模型网络。需要凭据的 smoke test 使用 `live_model` marker 并由开发者显式运行。

## Project Owner Tool 调试

优先使用真实 Tool Action 调试入口：

```bash
uv run python scripts/debug_tool_cli.py \
  --cwd .agentplanex/tests/<case> \
  --print \
  '{"tool":"bash","arguments":{"command":"pwd"}}'
```

测试生成的项目放在 `.agentplanex/tests/`，不要改变 AgentPanelX 仓库自身的 Git 状态。

## 提交内容

一个易于审查的改动应包含：

1. 用户可见的能力或问题描述；
2. 最小实现与清楚的模块边界；
3. 能证明真实行为的测试或浏览器证据；
4. 实际运行过的验证命令；
5. 已知限制和未完成事项。

公开提交不得包含内部求职材料、私人路径、模型额度、真实 request id 或未经脱敏的执行日志。
