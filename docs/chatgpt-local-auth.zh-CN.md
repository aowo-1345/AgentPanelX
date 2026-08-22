# 通过本地代理接入 ChatGPT 订阅

AgentPanelX 可以让可选的 `codex` Model Profile 指向用户自行管理的
[CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) 进程。CLIProxyAPI 是第三方兼容层，
不是 OpenAI 官方 Endpoint；AgentPanelX 不负责安装、认证、启动、监控或升级它。ChatGPT
订阅登录与 OpenAI Platform API Key 及其计费也是两套不同机制。

下面的路径已使用 CLIProxyAPI `v7.2.139` 验证：非流式 `/v1/responses`、原生 Function Call、
Project Owner 上下文续接和 Prompt Cache usage 均可工作。使用其他版本前请先查看上游项目。

## 1. 将代理状态保留在本地

在 AgentPanelX 仓库根目录创建以下已被 Git 忽略的路径：

```bash
mkdir -p .agentplanex/secrets/cliproxy/auth
chmod 700 .agentplanex/secrets/cliproxy .agentplanex/secrets/cliproxy/auth
```

创建 `.agentplanex/secrets/cliproxy/config.yaml`：

```yaml
host: "127.0.0.1"
port: 8317
tls:
  enable: false
remote-management:
  allow-remote: false
  disable-control-panel: true
auth-dir: "/root/.cli-proxy-api"
proxy-url: "" # 仅在主机确实需要时填写可信的出站代理。
api-keys:
  - "replace-with-a-long-local-access-key"
debug: false
```

`api-keys` 用于保护本地 HTTP Endpoint；它不是 OpenAI API Key，也不是 OAuth 凭据。不要提交
该配置或 `auth/` 目录。

## 2. 完成浏览器 OAuth

挂载持久化认证目录，并执行 CLIProxyAPI 提供的 Codex 浏览器登录：

```bash
docker run --rm -it --network host \
  -v "$PWD/.agentplanex/secrets/cliproxy/config.yaml:/CLIProxyAPI/config.yaml:ro" \
  -v "$PWD/.agentplanex/secrets/cliproxy/auth:/root/.cli-proxy-api" \
  eceasy/cli-proxy-api:v7.2.139 \
  ./CLIProxyAPI -config /CLIProxyAPI/config.yaml -codex-login
```

如果 CLIProxyAPI 运行在远程 Linux 主机上，请在浏览器所在电脑保持 SSH 本地转发：

```bash
ssh -L 1455:127.0.0.1:1455 USER@HOST
```

随后在登录命令末尾增加 `-no-browser -oauth-callback-port 1455`，并在本地浏览器打开命令打印
的授权 URL。只有当 CLIProxyAPI 报告已有授权缺失、过期或被撤销时，才需要重新执行 OAuth。

## 3. 运行代理并选择 Profile

请使用你自己的进程管理方式启动这个第三方服务。下面是已验证环境对应的前台 Docker 形式：

```bash
docker run --rm --network host \
  -v "$PWD/.agentplanex/secrets/cliproxy/config.yaml:/CLIProxyAPI/config.yaml:ro" \
  -v "$PWD/.agentplanex/secrets/cliproxy/auth:/root/.cli-proxy-api" \
  eceasy/cli-proxy-api:v7.2.139 \
  ./CLIProxyAPI -config /CLIProxyAPI/config.yaml
```

仓库已在 `config/settings.yaml` 中声明可选 Profile：

```yaml
project_owner_agent:
  active_model: "codex"
  models:
    codex:
      adapter: "openai"
      name: "gpt-5.6-luna"
      base_url: "http://127.0.0.1:8317/v1"
      api_key_env: "CLIPROXY_API_KEY"
      reasoning_effort: "high"
      service_tier: null
```

在本地配置中把 `active_model` 改为 `codex`，并导出与代理配置相同的本地访问 Key：

```bash
export CLIPROXY_API_KEY='your-local-access-key'
uv run agentplanex-web
```

仓库提交的默认值仍是 `qwen`。AgentPanelX 只在启动时绑定一个 Adapter，不会主动探测或跨
Provider 回退。

## 4. 验证一次真实 Activation

通过正常 Web Console 或 Runtime Control 路径向 Project Owner 发送 Message，并确认收到回复。
随后检查普通文本日志：

```bash
rg 'event=model_gateway_call adapter=openai' ".logs/agentplanex-$(date +%F).log"
rg 'cached_tokens=[1-9][0-9]*' ".logs/agentplanex-$(date +%F).log"
```

Cache 命中可能需要多次满足 Provider 资格且具有足够长公共前缀的请求。日志只包含状态、耗时与
Token 数，不包含 Prompt、Response、Tool output、call ID、request ID、OAuth 资料或缓存亲和值。

开发者可以显式运行隔离的 credentialed journey；默认测试仍会排除它：

```bash
AGENTPLANEX_RUN_LIVE_MODEL=1 \
uv run pytest -o addopts='' -m 'live_model and e2e' \
  tests/test_model_gateway_live.py -q
```
