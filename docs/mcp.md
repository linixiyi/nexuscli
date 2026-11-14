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
NEXUSCLI_MCP_API_KEY=<your-key> uv run nexuscli mcp serve --transport http --port 3000
```

HTTP 传输要求配置 `NEXUSCLI_MCP_API_KEY`：未配置时服务器拒绝启动，请求缺 key 或
错 key 一律 401（`Authorization: Bearer <key>` 或 `x-api-key: <key>` 头）。stdio
传输不经网络、无此要求。

HTTP smoke（带 key）：

```bash
curl -sS -X POST http://127.0.0.1:3000 \
  -H 'content-type: application/json' \
  -H 'authorization: Bearer <your-key>' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

> ⚠️ **部署边界**：作为 MCP server 运行时，NexusCLI 会在协议层强制关闭人工审批（`hitl_mode = "never"`），否则无法应答远端 `tools/call` 请求。这意味着接入该 server 的任何客户端都获得了**无需审批的完整工具能力（包括执行命令、读写文件）**。`tools/call` 走与其他入口相同的 ToolExecutor 门序（审计日志、hooks、permission 规则、写域守卫均生效），且 HTTP 传输必须配置 `NEXUSCLI_MCP_API_KEY` 凭证门（缺/错 key 一律 401）。只把 server 与 key 暴露给可信客户端；HTTP 传输默认只绑定 localhost，不要手动开放到公网。 本项目按 AGPL-3.0-only 授权：把 MCP server（或其修改版）作为网络服务提供时，依 AGPLv3 §13 须向交互用户提供对应源码（自托管随仓库分发即满足）。


Chrome DevTools MCP 会把浏览器页面和 DevTools 状态暴露给 Agent。不要随意把包含个人账号、敏感数据或生产后台的 Chrome 会话授权给 Agent。

## 项目层 MCP 的信任门与环境清洗

`.nexuscli/mcp.json` 与项目 config 安全键、hooks 一样属于项目层配置，其 server 必须通过**项目信任指纹**才会进入有效列表：一个 enabled server 会在本机拉起任意命令，克隆来的仓库可以借此静默取得执行面（实现：`src/nexuscli/mcp/config.py` 的 `_project_layer_servers`，对 `mcpServers` 映射做 canonical JSON + sha256，与 hooks 指纹同一形态）：

- 未信任时项目层整层不合并，只保留用户层（`~/.nexuscli/mcp.json`）的 server，并给出一次可见 warning（`McpTrustWarning`）；确认后按工作区记忆到 `~/.nexuscli/mcp-trust.json`
- `-p` / serve 等 headless 入口没有 TTY，一律失败关闭：无已信任指纹就不 spawn，信任确认只能由人交互完成
- 只含 `enabled: false` server 的项目层永远不会 spawn，不需要信任，照常合并展示
- 三个指纹存储（config 安全键 / MCP / hooks）彼此独立：信任其中一个不连带授权另外两个，撤销也互不影响

stdio server 的子进程环境是**清洗后的环境**，不再是全量 `os.environ` 叠加 `spec.env`（实现：`src/nexuscli/mcp/client.py` 用 `sanitize_environment(overrides=spec.env)` 构造 `StdioServerParameters.env`）：名字含 `KEY` / `SECRET` / `TOKEN` / `PASSWORD` / `CREDENTIAL` 等密钥形态的变量不再传给 server 子进程，`PATH` / `HOME` 等运行必需键保留，`spec.env` 的显式值原样保留在最上层——server 合同需要的变量请在 `spec.env` 里显式声明。清洗语义与如实边界见 [permissions.md](permissions.md#子进程环境清洗env_sanitization)。

## 工具级过滤（allowed_tools / blocked_tools）

MCP server 常暴露几十上百个工具，全量注入会污染上下文并放大误调用面。每个 server 条目可以单独配置过滤（实现：`src/nexuscli/mcp/config.py` 的 `McpServerSpec`，过滤逻辑在 `src/nexuscli/mcp/client.py` 的 `_tool_registered`）：

```json
{
  "mcpServers": {
    "github": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "allowed_tools": ["get_*", "list_*", "create_issue"],
      "blocked_tools": ["delete_*", "admin_*"]
    }
  }
}
```

字段与行为，全部以实现为准：

- **`allowed_tools`（白名单，优先）**：非空时只有匹配列表中至少一个模式的远端工具会注册。
- **`blocked_tools`（黑名单）**：`allowed_tools` 为空时生效，命中任一模式的远端工具被剔除。
- **匹配规则**：精确工具名或 fnmatch 通配（`*` / `?`），大小写敏感；精确名不会被同前缀通配误吞（白名单里写 `echo` 不会匹配 `echo2`，需要显式写 `echo*`）。
- **两个列表都必须是 JSON 数组**。写错类型（如把列表误写成字符串）按「未配置」处理——不过滤、全部注册，不会静默吞掉工具；这是刻意的容错方向，宁可少过滤也不静默禁用。
- **virtual 工具不受过滤影响**：每个 server 附带的 `list_resources` / `read_resource` / `list_prompts` / `get_prompt` 四个包装工具始终注册；远端工具全被过滤时只是没有远端工具入表，不报错。
- **与 `enabled` 开关叠加**：`enabled: false` 的 server 整体跳过，过滤只对启用的 server 生效；不配置两个列表时行为与从前完全一致。

## readOnlyHint 与只读语义

MCP 协议允许 server 在工具的 `annotations.readOnlyHint` 里自述「只读」。MCP server
是外部进程，这个字段是**远端的自述，不是本地可信声明**——一个被投毒或恶意的
server 完全可以声明只读、然后删文件。因此 NexusCLI 不把 `readOnlyHint` 当作安全
判定（实现：`src/nexuscli/mcp/client.py` 的 `_tools_for_server`）：

- **远端工具一律按有副作用注册**：`is_read_only=False`、`requires_approval=True`、
  `danger_level="medium"`，与 hint 真假无关。plan 模式拒绝执行远端 MCP 工具；
  auto/default 模式照常走审批链询问；审计按非只读工具记录。
- **`readOnlyHint=True` 只进展示面**：作为附注追加到工具描述（"server advertises
  readOnlyHint; NexusCLI does not trust it and still requires approval"），供模型与
  用户参考，不参与 `is_read_only` / `requires_approval` / `danger_level` 的计算。
- **virtual 资源/提示工具不走本地免审批**：`read_resource` / `get_prompt` 需要审批并进审计——它们由 NexusCLI 本地包装，但返回内容来自外部 server 进程，与远端工具输出同样不可信，因此注册为非只读、`requires_approval=True`、`danger_level="medium"`（实现：`src/nexuscli/mcp/client.py`）；auto/default 模式照常走审批链询问，plan 模式与远端工具一样拒绝 `read_resource` / `get_prompt`。纯列目录的 `list_resources` / `list_prompts` 保持只读，不弹审批。

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
