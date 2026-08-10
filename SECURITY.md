# Security

AgentPanelX 当前是本地开发工具，不是云端多租户服务。请只在受信任的开发环境和目标仓库中运行。

## 报告问题

请不要在公开 Issue 中提交 API Key、模型请求详情、私人仓库内容或可识别用户的 Runtime 数据。公开仓库发布后，请通过仓库维护者提供的私密安全报告渠道提交漏洞；在渠道建立前，避免公开披露可直接利用的细节。

## 当前边界

- Web server 默认只绑定 `127.0.0.1` / `localhost`。
- 模型凭据从环境变量读取，不应写入 `config/settings.yaml`、数据库、截图或日志。
- Project Owner Bash 使用 Bubblewrap 限制持久写入范围并创建无网络 namespace。
- Bubblewrap 边界主要限制写入和网络；它仍可能读取宿主文件，因此不能视为保密沙箱。
- AgentPanelX 会在目标 Git 仓库创建 managed worktree、Feature branch 和 project-local `.agentplanex` Runtime 数据。
- `.agentplanex` 会写入目标仓库私有 `.git/info/exclude`，但开发者仍应在公开提交前检查 Git status。
- Control Skill 只应操作用户明确授权的真实项目，并且必须经过 Project Runtime；禁止直接修改 SQLite 或 Git ref。

## 不应提交的内容

- API Key、Authorization header 或含凭据的 `.env`；
- `.agentplanex/*.sqlite3`、trajectory、transcript、模型 request id；
- 未经脱敏的目标仓库路径、用户名、消息或 Tool output；
- 临时 worktree、浏览器 trace 和内部调试 artifact。

如果怀疑凭据已经进入 Git 历史，请先轮换凭据，再使用经过审查的历史清理流程；删除工作区文件本身不足以撤销泄露。
