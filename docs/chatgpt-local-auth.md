# ChatGPT subscription through a local proxy

AgentPanelX can point its optional `codex` Model Profile at a user-managed
[CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) process. CLIProxyAPI is a
third-party compatibility layer; it is not an OpenAI endpoint, and AgentPanelX does not
install, authenticate, start, monitor, or upgrade it. A ChatGPT subscription login is also
separate from an OpenAI Platform API key and its billing.

The path below was verified with CLIProxyAPI `v7.2.139`, its non-streaming `/v1/responses`
endpoint, native function calls, continued Project Owner context, and prompt-cache usage.
Check the upstream project before using a different release.

## 1. Keep proxy state local

From the AgentPanelX repository root, create these Git-ignored paths:

```bash
mkdir -p .agentplanex/secrets/cliproxy/auth
chmod 700 .agentplanex/secrets/cliproxy .agentplanex/secrets/cliproxy/auth
```

Create `.agentplanex/secrets/cliproxy/config.yaml`:

```yaml
host: "127.0.0.1"
port: 8317
tls:
  enable: false
remote-management:
  allow-remote: false
  disable-control-panel: true
auth-dir: "/root/.cli-proxy-api"
proxy-url: "" # Set a trusted outbound proxy only when your host requires one.
api-keys:
  - "replace-with-a-long-local-access-key"
debug: false
```

The `api-keys` entry protects the local HTTP endpoint. It is not an OpenAI API key and is
not the OAuth credential. Never commit the config or `auth/` directory.

## 2. Complete browser OAuth

Run CLIProxyAPI's browser-based Codex login while mounting the persistent auth directory:

```bash
docker run --rm -it --network host \
  -v "$PWD/.agentplanex/secrets/cliproxy/config.yaml:/CLIProxyAPI/config.yaml:ro" \
  -v "$PWD/.agentplanex/secrets/cliproxy/auth:/root/.cli-proxy-api" \
  eceasy/cli-proxy-api:v7.2.139 \
  ./CLIProxyAPI -config /CLIProxyAPI/config.yaml -codex-login
```

For a remote Linux host, keep an SSH local forward open from the browser machine and make
the callback port explicit:

```bash
ssh -L 1455:127.0.0.1:1455 USER@HOST
```

Then add `-no-browser -oauth-callback-port 1455` to the login command and open the printed
authorization URL in the local browser. Repeat OAuth only when CLIProxyAPI reports that the
stored authorization is missing, expired, or revoked.

## 3. Run the proxy and select the profile

Start the third-party process using your preferred process manager. This foreground Docker
shape matches the verified setup:

```bash
docker run --rm --network host \
  -v "$PWD/.agentplanex/secrets/cliproxy/config.yaml:/CLIProxyAPI/config.yaml:ro" \
  -v "$PWD/.agentplanex/secrets/cliproxy/auth:/root/.cli-proxy-api" \
  eceasy/cli-proxy-api:v7.2.139 \
  ./CLIProxyAPI -config /CLIProxyAPI/config.yaml
```

The repository already declares the optional profile in `config/settings.yaml`:

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

Set `active_model` to `codex` in your local config. AgentPanelX first reads
`CLIPROXY_API_KEY` from its process environment; when it is unset, the local `codex`
profile automatically uses the first non-blank key from
`.agentplanex/secrets/cliproxy/config.yaml`:

```bash
export CLIPROXY_API_KEY='your-local-access-key'
uv run agentplanex-web
```

The explicit environment variable remains useful for service managers, containers, remote
proxies, and temporary overrides. It always takes precedence over the local file.

The committed default remains `qwen`. AgentPanelX binds exactly one Adapter at startup; it
does not probe or fall back to another provider.

## 4. Verify one real activation

Send a Message to a Project Owner through the normal Web Console or Runtime control path and
confirm that it replies. Then inspect the ordinary text log:

```bash
rg 'event=model_gateway_call adapter=openai' ".logs/agentplanex-$(date +%F).log"
rg 'cached_tokens=[1-9][0-9]*' ".logs/agentplanex-$(date +%F).log"
```

A cache hit may require multiple eligible requests with a sufficiently long shared prefix.
Logs contain status, duration, and Token counts, but not Prompts, Responses, Tool output,
call IDs, request IDs, OAuth data, or the cache-affinity value.

Developers can run the isolated credentialed journey explicitly; the default test suite
continues to exclude it:

```bash
AGENTPLANEX_RUN_LIVE_MODEL=1 \
uv run pytest -o addopts='' -m 'live_model and e2e' \
  tests/test_model_gateway_live.py -q
```
