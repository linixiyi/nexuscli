# Hooks 生命周期钩子

> 本文是 [README](../README.md)「🪝 Hooks 生命周期钩子」章节的完整版。

在 config.json 的 `hooks` 里给七个生命周期事件挂 shell 命令（事件键为 camelCase）：

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "bash|write_file",
        "hooks": [
          { "type": "command", "command": "python .nexuscli/hooks/guard.py", "timeout": 10 }
        ]
      }
    ],
    "Stop": [
      { "matcher": "*", "hooks": [{ "type": "command", "command": "notify-send done" }] }
    ]
  }
}
```

- 七个事件：`SessionStart`、`UserPromptSubmit`、`PreToolUse`、`PermissionRequest`、`PostToolUse`、`PostToolUseFailure`、`Stop`；`matcher` 是对工具名的正则（默认 `*` 全匹配，仅工具事件使用）
- `PermissionRequest` 在审批决策前触发，可 deny/ask，payload 含 `tool_name` / `tool_input`；`PostToolUseFailure` 在工具失败时触发，payload 含 `tool_name` / `tool_input` / `error_message`，失败回合不再触发 `PostToolUse`
- 每条 hook 是 `{"type": "command", "command": "...", "timeout": 60}`；进程 stdin 收到 JSON 载荷（`hook_event_name`、`session_id`、`cwd`，工具事件另有 `tool_name` / `tool_input`，`PostToolUse` 另有 `tool_response`，`PostToolUseFailure` 另有 `error_message`，`UserPromptSubmit` 另有 `prompt`）
- 协议：**exit 2 阻断**（stderr 作为拒绝原因），exit 0 时 stdout 可输出 JSON（`decision: block` 或 `hookSpecificOutput.permissionDecision: deny/ask`）同样生效；超时与其他退出码只作为非阻断错误提示
- 安全边界：hook 返回 `allow` 只做记录，**不会绕过审批**——hooks 只能拒绝、追问或补充上下文，不能替人工放行
- 未配置对应事件时零开销；用户级与项目级的 hook 列表同样按层拼接

## 工作区 hooks 的 sha256 信任机制

项目级 `.nexuscli/config.json` 的 `hooks` 段是随仓库分发、加载时无条件合并的，克隆一个恶意仓库等于让任意 shell 命令挂在每个工具事件上。为此 REPL 在启动时对工作区 hooks 加了一道信任闸门（实现：`src/nexuscli/hooks/trust.py` 与 `repl._gate_workspace_hooks`）：

1. **指纹**：对项目层整个 `hooks` 段做规范化 JSON（键排序、紧凑分隔符）后取 sha256，文件里键序/缩进不同但内容相同的配置得到同一指纹。
2. **一次性确认**：首次遇到某工作区的未信任指纹时提示 `Workspace hooks detected ...` 并询问 `Trust workspace hooks? [y/n]`（默认 n）。同意后把 `{工作区路径: {fingerprint, trusted_at}}` 记入 `~/.nexuscli/hooks-trust.json`；同一工作区+同一指纹后续启动不再询问。
3. **拒绝的后果**：拒绝时本次会话把生效 hooks 替换为用户级 hooks（工作区 hooks 被剥离禁用），用户级 hooks 不受任何影响。
4. **变更即重审**：信任记录按指纹匹配，hooks 内容一变（哪怕只改一条命令）指纹即变，下次启动重新确认。
5. **非交互环境**：stdin 不是 TTY 时一律保守拒绝（与审批提示同一姿态），不静默放行。

存储边界：信任文件只存指纹与时间戳，不存 hook 命令、工作区内容或任何秘密；文件以 best-effort `chmod 0600` 保护（NTFS 上退化为只读位，靠用户目录 ACL 兜底）。已知边界：该闸门目前只挂在交互式 REPL 入口，`nexuscli -p` 单次执行与 serve 路径不在本切片范围内——不信任的工作区请勿用 `-p` 执行。
