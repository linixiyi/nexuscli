# MCP

> 本文是 [README](../README.md)「🔌 MCP」章节的完整版。

NexusCLI 可以连接 MCP server，并把远端工具动态注册为：

```text
mcp__<server-name>__<tool-name>
```

初始化项目级 Chrome DevTools MCP 配置：

```bash
uv run nexuscli mcp init-chrome --scope project
```

它会写入 `.nexuscli/mcp.json`，内容类似：

```json
{
  "mcpServers": {
    "chrome-devtools": {
      "type": "stdio",
      "command": "npx",
      "args": [
        "-y",
        "chrome-devtools-mcp@latest",
        "--no-usage-statistics"
      ]
    }
  }
}
```

连接已有 remote-debugging Chrome：

```bash
uv run nexuscli mcp init-chrome \
  --scope project \
  --browser-url http://127.0.0.1:9222
```

查看已配置的 MCP server：

```bash
uv run nexuscli mcp list
```

把 NexusCLI 自身作为 MCP server 暴露：

```bash
uv run nexuscli mcp serve --transport stdio
uv run nexuscli mcp serve --transport http --port 3000
```

HTTP smoke：

```bash
curl -sS -X POST http://127.0.0.1:3000 \
  -H 'content-type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

> ⚠️ **部署边界**：作为 MCP server 运行时，NexusCLI 会在协议层强制关闭人工审批（`hitl_mode = "never"`），否则无法应答远端 `tools/call` 请求。这意味着接入该 server 的任何客户端都获得了**无需审批的完整工具能力（包括执行命令、读写文件）**。只把 server 暴露给可信客户端；HTTP 传输默认只绑定 localhost，不要手动开放到公网。

Chrome DevTools MCP 会把浏览器页面和 DevTools 状态暴露给 Agent。不要随意把包含个人账号、敏感数据或生产后台的 Chrome 会话授权给 Agent。

## OAuth 2.0 授权（HTTP 类 server，授权码 + PKCE）

`type: http` / `streamable_http` 的 server 支持 OAuth 2.0 public client 授权。在 `mcp.json` 的 server 条目里加 `auth` 段（实现：`src/nexuscli/mcp/config.py` 的 `McpAuthConfig`）：

```json
{
  "mcpServers": {
    "acme-api": {
      "type": "streamable_http",
      "url": "https://mcp.example.com/mcp",
      "auth": {
        "type": "oauth",
        "client_id": "<your-public-client-id>",
        "authorize_url": "https://auth.example.com/authorize",
        "token_url": "https://auth.example.com/token",
        "redirect_uri": "http://localhost:8765/callback",
        "scopes": ["mcp:read", "mcp:write"]
      }
    }
  }
}
```

字段与行为，全部以实现为准：

- **public client，无 `client_secret`**：设计上就不存在该字段，机器秘密不能写进 `mcp.json`；真实凭据只来自环境或授权流程本身。未知/非法的 `auth` 段会被安全忽略（server 照常按无 auth 加载）。
- **自动附带令牌**：连接时从令牌缓存读取 access token 并附 `Authorization: Bearer <token>` 头（`src/nexuscli/mcp/client.py`）。
- **401 自动刷新重试一次**：会话建立遇 401 且存有 refresh_token 时，自动刷新令牌并重试**一次**；没有 refresh_token 或刷新失败则以原始 401 报错。会话已建立后的失败不在重试范围内。
- **令牌落盘**：`~/.nexuscli/mcp-oauth.json`，按 server 名分条存储 `access_token` / `refresh_token` / `expires_at`，best-effort `chmod 0600`（Windows 上靠用户目录 ACL 兜底）；令牌响应字段不会写入日志。过期前 30 秒内的时钟偏移会触发提前刷新。
- **PKCE**：code verifier 为 43–128 位 RFC 7636 无保留字符集随机串，challenge 为 S256；令牌端点 URL 有 SSRF 守卫（仅 http/https，拒绝 localhost/环回/私有/保留地址字面量；不做 DNS 解析）。

**如实边界**：本切片只提供令牌原语与自动续期（PKCE 生成、authorize URL 拼装、换码、刷新、存储），**不含**交互式授权流程——浏览器拉起、localhost 回调监听、RFC 8414 发现、动态客户端注册都未实现。首个 access token 需要在 NexusCLI 之外完成授权（例如用你自己的脚本走一遍授权码流程）后写入令牌文件；此后 NexusCLI 负责附带、过期刷新与 401 重试。`redirect_uri` 默认值只是占位，本切片不会启动回调监听。
