# 权限规则

> 本文是 [README](../README.md)「🔐 权限规则」章节的完整版。

在 config.json 的 `permissions` 里声明 `allow` / `deny` / `ask` 三组规则，就能在 HITL 之前自动放行安全操作、追问可疑调用或直接拦截高危动作：

```json
{
  "permissions": {
    "allow": [
      "read_file",
      "bash(git diff:*)",
      "bash(npm run *)",
      "write_file(src/**)"
    ],
    "ask": ["web_fetch(domain:github.com)"],
    "deny": ["bash(curl | sh)", "mcp__github__delete_*"]
  }
}
```

规则写法（`工具(参数)` 或裸工具名）：

| 写法 | 匹配逻辑 |
|---|---|
| `read_file` | 该工具的每次调用 |
| `bash(git diff:*)` | 命令前缀匹配（`:*` 结尾表示"以此开头"） |
| `bash(npm run *)` | 对完整命令串做 fnmatch 通配 |
| `write_file(src/**)` | 对工具载荷里的路径做 fnmatch 通配 |
| `web_fetch(domain:github.com)` | 域名精确或子域匹配 |
| `mcp__github__*` | 工具名支持 `*` / `?` 通配 |

评估优先级是 **deny > ask > allow**：

- `deny` 命中立即拒绝，错误信息与审计日志都带规则原文（approver 记为 `permission-rule`）
- `ask` 强制走人工确认，即使同一次调用也命中了更宽的 `allow`
- `allow` 命中时跳过审批弹窗；但 `hitl_mode: "always"`（逐切确认）下仍会提示；`hitl_mode: "never"` 时 `ask` 按失败关闭原则直接拒绝

规则与 HITL 一样按层拼接：用户级和项目级 config.json 的同名列表会合并去重，项目规则不会覆盖掉用户级 `deny`。

## 只读 bash 直通

`bash` / `execute_command` 的命令串会先做一次 argv 级只读静态判定。判定是保守的：只认单条简单命令（含 `;`、`|`、重定向、命令替换等元字符时一律视为不只读），并基于内置白名单——`ls`、`cat`、`grep`、`head` 等检查类命令，不带写动作参数的 `find`，只读子命令的 `git`（`status`、`log`、`diff`、`show` 等，`branch` / `tag` / `remote` / `config` 另有参数限制），以及 `python` / `node` / `npm` 的 `--version` / `--help` 式自述调用与 `uv` / `pip` 的列表类子命令；白名单之外的命令一律视为不只读，回落到正常审批链。

判定为只读的命令在默认模式下免 HITL 审批直通执行，`plan` 态的只读闸门也按同一判定放行。直通不改变既有优先级：`deny` 命中照常拒绝，`ask` 命中照常追问，PreToolUse hook 的阻断与 ask 提示同样优先，`hitl_mode: "always"` 下仍会弹审批。直通不等于无痕：直通调用仍写审计日志，approver 记为 `readonly-rule`。
