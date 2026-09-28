# Hooks 生命周期钩子

> 本文是 [README](../README.md)「🪝 Hooks 生命周期钩子」章节的完整版。

在 config.json 的 `hooks` 里给五个生命周期事件挂 shell 命令（事件键为 camelCase）：

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

- 五个事件：`SessionStart`、`UserPromptSubmit`、`PreToolUse`、`PostToolUse`、`Stop`；`matcher` 是对工具名的正则（默认 `*` 全匹配，仅工具事件使用）
- 每条 hook 是 `{"type": "command", "command": "...", "timeout": 60}`；进程 stdin 收到 JSON 载荷（`hook_event_name`、`session_id`、`cwd`，工具事件另有 `tool_name` / `tool_input`，`PostToolUse` 另有 `tool_response`，`UserPromptSubmit` 另有 `prompt`）
- 协议：**exit 2 阻断**（stderr 作为拒绝原因），exit 0 时 stdout 可输出 JSON（`decision: block` 或 `hookSpecificOutput.permissionDecision: deny/ask`）同样生效；超时与其他退出码只作为非阻断错误提示
- 安全边界：hook 返回 `allow` 只做记录，**不会绕过审批**——hooks 只能拒绝、追问或补充上下文，不能替人工放行
- 未配置对应事件时零开销；用户级与项目级的 hook 列表同样按层拼接
