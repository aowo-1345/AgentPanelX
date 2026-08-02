# 运行时数据参考

AgentPlaneX 组合三类权威事实：

- SQLite 记录结构化的项目控制关系。
- Git 记录规划提交、候选提交和配置的接受分支引用。
- `.agentplanex/` 文件记录长篇运行轨迹、交接记录、审查证据和日志。

Git 提交和证据文件不是数据库表。SQLite 只保存指向它们的 SHA、引用和路径。

## 工作区注册表

路径：`<project>/.agentplanex/workspace.db`

`triages` 表只有 `triage_id TEXT PRIMARY KEY` 和 `payload TEXT NOT NULL`。JSON 数据包含
`triage_id`、`idea`、`status`、`pending_action`、`base_commit_sha`、`branch_ref`、
`worktree_path`、`common_git_dir`、`project_id` 和 `created_at`。

使用 `triage_id` 选择 Triage，使用 `worktree_path` 定位其项目数据库。`project_id` 必须等于该数据库
保存的项目身份。`branch_ref` 是这个 Triage 运行时配置的接受分支引用。

## 项目数据库

路径：`<triage-worktree>/.agentplanex/project.db`

| 表 | 主键与索引字段 | JSON 数据含义 |
|---|---|---|
| `project_state` | `singleton=1` 主键、`project_id`、`current_snapshot_id` | 没有 JSON 数据；保存项目身份和当前快照指针。 |
| `snapshots` | `snapshot_id` 主键 | 不可变的 `previous_snapshot_id`、规划提交 SHA、发布时的接受分支 SHA、原因和完整有序的里程碑视图。 |
| `milestone_runs` | `run_id` 主键；`snapshot_id`、`milestone_key`、`status` | 一次执行尝试，包括接受基线 SHA、候选分支/工作树/最终提交、失败信息和时间。 |
| `stage_runs` | `stage_run_id` 主键；唯一 `(run_id, stage_key)` | 阶段输入/关卡提交、交接文件引用、执行者会话、租约/失败信息和时间。 |
| `review_tasks` | `review_task_id` 主键；`run_id` 唯一 | 固定的候选/基线 SHA、审查者会话、摘要、运行轨迹/结果文件引用和时间。 |

逻辑查询链路：

```text
workspace.triages.project_id -> project_state.project_id
project_state.current_snapshot_id -> snapshots.snapshot_id
snapshots.snapshot_id -> milestone_runs.snapshot_id
milestone_runs.run_id -> stage_runs.run_id
milestone_runs.run_id -> review_tasks.run_id
```

里程碑嵌在每个快照的 JSON 数据中，应使用 `(snapshot_id, milestone_key)` 定位；不存在里程碑表。
规划版本是快照的 `plan_version_commit_sha`；不存在规划表。

## Git 与交付接受关系

V1 配置的接受分支引用默认是 `main`。V2 Triage 应使用 `triages.payload` 中的 `branch_ref`。
常用只读检查命令：

```bash
git rev-parse <configured-accepted-ref>
git merge-base --is-ancestor <candidate-tip-sha> <configured-accepted-ref>
git show --stat <commit-sha>
git log --oneline --decorate <configured-accepted-ref>
```

执行记录的 `candidate_ready` 状态只能证明执行产生了候选结果。要证明已经接受交付，需要后继快照将
同一里程碑标记为 `completed`、审查证据指向完全相同的候选最终提交，并且 Git 事实与接受分支引用一致。
除非正在诊断这套推导，否则直接使用标准 inspect 视图中的
`accepted_delivery_mainline`。

## 证据文件

常见引用位于所选工作树的 `.agentplanex/` 目录下：

```text
supervisor/trajectory.json
planner/transcript.json
runs/<run-id>/stages/<stage-key>/handoff.md
runs/<run-id>/reviews/<review-task-id>/transcript.json
runs/<run-id>/reviews/<review-task-id>/result.md
```

按照阶段或审查 JSON 数据中保存的准确文件引用读取。引用文件缺失属于完整性问题；不要猜测其内容，
也不要通过直接修改数据库来修复。
