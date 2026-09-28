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
