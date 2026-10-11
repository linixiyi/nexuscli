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

## 审批弹窗的四个选项

HITL 审批弹窗（`bash`、写文件等需要人工确认的调用）提供五个键位，对应四档决策：

| 键 | 行为 |
|---|---|
| `y` | 放行本次调用（之后同工具再弹） |
| `r` | **run 级授权**：本次 agent run 内、该主代理上下文里，同工具后续本会弹窗的调用直接放行 |
| `a` | 切到 auto 模式（会话级放行，除非 `ask` 规则或 hook ask 提示仍强制追问） |
| `s` | 跳过本次调用（模型收到"被跳过"的 tool 结果） |
| `n` | 拒绝本次调用 |

run 级授权（`r`）只作用于「本来就会弹人工提示」的环节：`deny` 规则、`ask` 规则、PreToolUse / PermissionRequest hook 的 ask 提示与 `plan` 只读闸门都不受影响，仍按各自优先级生效。首按 `r` 的那次决策审计记为 `hitl`，之后被 run 级放行的调用记为 `run-scope`（与 `readonly-rule` 同风格）。

边界要写清：run 的语义是**一次 agent run 的主代理上下文**——REPL 里即一个用户回合触发的完整 react 循环（`Agent._run_react` 每次 run 都新建 ToolContext，授权随之清零）。子代理（subagent）有自己的 ToolContext，主代理按 `r` 放行的工具在子代理里仍会独立弹审批。需要会话级放行请用 `a`（auto 模式）或 `permissions.allow` 规则。

## 子进程环境清洗（env_sanitization）

`bash` / `execute_command` 与后台任务派生会把**清洗后的环境**传给子进程，而不是父进程的全量 `os.environ`（实现：`src/nexuscli/policy/sandbox.py` 的 `sanitize_environment`）：

- **剔除机密形态键**：变量名含 `KEY` / `SECRET` / `TOKEN` / `PASSWORD` / `CREDENTIAL` / `PRIVATE_KEY`（大小写不敏感子串）的键一律不传——一条 `env` 或 `printenv` 命令不再能读走宿主进程携带的 API key。
- **剔除内部变量**：`NEXUSCLI_*` 前缀的 CLI 自身配置变量不传。
- **运行必需键强制保留**：`PATH` / `PATHEXT` / `HOME` / `USERPROFILE` / `TEMP` / `TMP` / `SYSTEMROOT` / `COMSPEC` 等 18 个键即使命中上面的模式也保留，保证子进程能启动。
- **宁删勿漏**：良性名字碰巧含模式（如 `MONKEY`）也会被删——少一个变量是可见的 bug，漏一把 key 不是。

config.json 的 `policy.env_sanitization` 开关（默认 `true`）控制该护栏；设为 `false` 退回全量继承的旧行为。

**如实边界：这是护栏，不是沙箱。** 它是「黑名单 + 安全网」，不是能力系统——名字不带机密形态的变量照样传给子进程，防的是意外泄漏的地板，不是恶意子进程。当前只清洗两个 agent 面向的 spawn 点（前台 `bash` 与后台任务注册表）。**仍未清洗**的子进程路径，按覆盖面挂账而非缺陷列出：

| 路径 | 现状 | 说明 |
|---|---|---|
| MCP stdio server | 继承全量 `os.environ`（叠加 `spec.env`） | server 合同上常需环境变量，直接清洗会破坏既有配置；接前需按 server 设计 allowlist |
| hooks runner | 继承全量 `os.environ` | 待接入 |
| slash 命令 `` !`cmd` `` 注入执行器 | 继承全量 `os.environ` | 待接入 |

OS 级沙箱（容器、受限令牌）是更大体量的外部依赖，同样挂账；在它落地之前，不要把宿主机上不可泄露的机密放在子进程可读取的环境里并放开 HITL。
