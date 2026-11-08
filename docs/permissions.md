# 权限规则

> 本文是 [README](../README.md)「🔐 权限与会话模式」章节的完整版。

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

## 单次 `-p` 模式下没有审批弹窗

交互式 REPL 才有 HITL 弹窗；`nexuscli -p` 的 `ToolContext` 不带审批回调，因此**需要审批的工具在该模式下按失败关闭处理**：`bash` / `execute_command`（非只读命令）、`write_file`、`edit_file`、`task`、`save_memory`、`save_skill`、`task_stop`、`revert_turn` 都会被拒绝，模型收到的 tool 结果形如：

```text
Tool "bash" was denied by approval policy. This run has no interactive approver
(single-prompt mode), so no human could be asked. Allow the tool beforehand with
permissions.allow (for example {"allow": ["bash"]}) in config.json, or run the
interactive REPL for approval prompts.
```

只读工具（`read_file`、`grep`、`glob`、`search_code`、只读 bash 命令等）不受影响。脚本化场景要真正执行写类动作，必须在 config.json 里显式声明：

```json
{ "permissions": { "allow": ["bash", "write_file"] } }
```

这条边界和 `ask` 在 `hitl_mode: "never"` 下的处理是同一姿态：**无人可问时按失败关闭，而不是静默放行**。注意 `permissions.allow` 只是放行审批层，`path_guard` 仍在生效——工作区外的写入依旧会被 `path escapes workspace` 拒绝。

### headless 状态与退出码（`status` / `--fail-on-blocked`）

`-p` 的每次 run 都以 `status` 收场，供脚本与 CI 机读（`nexuscli.agent.results.gate_denial_reason` 负责把执行器的拒绝文案归类）：

| status | 含义 |
|---|---|
| `ok` | 正常完成；门拒绝过的调用不存在 |
| `blocked` | 审批拒绝 / 权限规则、hook 拦截 / plan 只读闸拦下任务——run 已结束但目标未达成，`blocked_reason` 带第一条拒绝原文 |
| `failed` | run 中途抛异常 |

读取方式：

- `-p --json`：payload 增加 `status` 字段（`ok` 时没有 `blocked_reason`）；异常路径也照常吐一个 JSON 对象（`status: "failed"`，`error` 字段带异常文本，usage/cost 归零或 `null`）。
- 非 JSON 模式：stderr 打印一行 `status: ok` / `status: blocked (<拒绝原文>)` / `status: failed (<异常>)`，stdout 仍保持纯回答文本。
- 退出码默认**保持兼容**：`ok` 与 react 门拒绝（过去就静默通过）为 0，异常为 1，被 plan 只读闸拦下的计划按 0034 口径为 1；新增 `--fail-on-blocked` 后，`blocked` / `failed` 一律 exit 2（会覆盖上述 1），适合 CI 直接以退出码判失败。

注意边界：`status: "blocked"` 是「run 结束了但有门拒绝」——模型收到拒绝后仍可能给出正常文本回答。`team` 模式下 worker 的原始 `tool_result` 不出 SubAgent 折叠层，由编排器把门拒绝（同一张 `gate_denial_reason` 标记表归类）以 `gate_denial` 事件转发到外层流，因此 `-p --mode team` 与 react 一样报 `blocked` 并带拒绝原文；react 门拒绝与 team 门拒绝的默认退出码同为 0（过去就静默通过），`--fail-on-blocked` 时为 2。

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

## OS 级沙箱（sandbox.backend）

`config.json` 的 `sandbox` 段为命令执行提供 OS 级围栏（实现：`src/nexuscli/policy/os_sandbox.py`，协议为 `SandboxBackend`——`available` / `probe` / `wrap` 三动词）：

```json
{
  "sandbox": {
    "backend": "seatbelt",
    "writable_paths": ["/opt/shared-data"],
    "allow_network": false
  }
}
```

| `backend` | 行为 |
|---|---|
| `none`（默认） | 不做 OS 级围栏，行为与没有该模块时完全一致（仅保留 env 清洗等既有护栏） |
| `seatbelt` | macOS 上用 `sandbox-exec -p` 包装命令：写域限制在命令 cwd、系统临时目录、`/dev/null` 与显式 `writable_paths` 之内；`allow_network: false`（默认）时整网拒绝，`true` 时仅放行出站（bind/入站仍拒绝） |
| `bubblewrap` | Linux 上用 `bwrap` 挂载命名空间包装命令：根文件系统只读绑定，另挂全新 `/dev`、`/proc` 与 `/dev/shm`，仅 cwd、系统临时目录与显式 `writable_paths` 可写；`allow_network: false`（默认）时 `--unshare-net` 整网拒绝（含 loopback），`true` 时完全放开（bind 也放行，粒度比 seatbelt 粗）。需要非特权的 user namespaces（或 setuid `bwrap`），容器里常见两者皆无——probe 会如实报错。安装提示：Debian/Ubuntu `apt install bubblewrap`、Fedora `dnf install bubblewrap`、Arch `pacman -S bubblewrap` |
| `landlock` | Linux 上用内核 Landlock（实现：`src/nexuscli/policy/linux_sandbox.py`）包装命令：内置 helper 先对自身 `restrict_self` 再 exec 命令，写域与 seatbelt 相同（`/dev/null`、临时目录、cwd、显式 `writable_paths`），读保持开放；网络拒绝需要内核 ABI ≥ 4（Linux ≥ 6.7），仅覆盖 TCP（UDP 自 ABI 10 起），内核达不到时按 fail-closed 直接报错而非裸跑。ABI 探测是纯查询（`landlock_create_ruleset` 的 VERSION 标志），不会限制调用方本身；restrict 在一次性子进程里完成 |

**Linux 后端优先级**：两个 Linux 后端**优先 bubblewrap**（覆盖更完整、网络粒度更明确），`nexuscli.policy.linux_sandbox.preferred_linux_backend` 返回本机可用的首选后端；任一后端不可用时，typed 错误信息会带上安装提示，并在对方可用时提示切换 `sandbox.backend`。

**fail-closed 语义**：请求了本机不可用的后端（非 macOS 请求 seatbelt、`sandbox-exec` 不在 PATH、probe 试运行失败；非 Linux 或 `bwrap` 缺失/不可用时请求 bubblewrap；非 Linux 或内核 Landlock ABI 不足时请求 landlock）或未知后端名时，抛类型化错误 `nexuscli.policy.os_sandbox.OperationUnsupported`，命令**不执行**——绝不会静默退回 `none` 继续裸跑。`CommandExecutor` 在构造时完成解析，坏配置在任何命令 spawn 之前就失败。

`writable_paths` 追加可写子域；cwd 与系统临时目录始终可写。`sandbox-exec` 已被 Apple 标记废弃但仍是官方策略机制；后端不可用会以 `OperationUnsupported` 显式报错，而不是产生一个坏掉的 spawn。bubblewrap 会丢弃本机不存在的可写路径（无物可写、也无法绑定）；landlock helper 对打不开的规则路径跳过并在 stderr 告警。

**如实边界：这是「最小可用」围栏，不是硬安全边界。** 文件读与 Mach lookup 保持开放（加载解释器、site-packages、launchd 服务必须能工作），所以恶意子进程仍能读走用户可读的一切、与 XPC 服务通信；围栏的目标是**写域与网络**。按最小可用原则枚举 mach 服务不可维护，profile 据此取舍。Linux 后端同理：环境变量、IPC、TTY ioctl、路径 UNIX socket 解析（Landlock `RESOLVE_UNIX`）等仍开放，landlock 的网络开关在 ABI 4–9 内核上不覆盖 UDP。

**接入现状**：`CommandExecutor`（`!` 注入执行器的执行引擎、库内嵌调用方）已支持——构造时传入 `sandbox` 配置即生效；模型面 `bash` 工具（含 `execute_command` 别名）已接线——从 `context.config.sandbox` 读取配置，在命令守卫之后、spawn 之前经 `wrap_command` 包装，`run_in_background` 后台任务与前台进程同路径生效，后端不可用时在 spawn 之前 fail-closed 报错（`Command rejected. ...`，命令不执行），默认 `backend="none"` 行为与接线前完全一致。仍未接的调用方：slash 命令注入的 `CommandExecutor` 构造未传 sandbox 配置（交互式用户直接执行的路径），属后续任务。

## 会话权限模式（Default / Auto / plan）

交互模式下按 `Shift+Tab` 可在三种会话权限模式间循环切换（`Default → Auto (full access) → plan (read-only) → Default`）：

- `Default`：使用启动时的 HITL、工作区路径和命令安全策略。
- `Auto (full access)`：当前会话内不再请求审批，并关闭路径与命令守卫（写域守卫亦随守卫关闭，域外写不再单独追问；headless 无审批回调时仍 fail-closed）。
- `plan (read-only)`：只读约束态，非只读工具被硬拒绝；再次按 `Shift+Tab` 回到默认策略。

plan 态不是免审批态：它会恢复启动时的 HITL 与安全策略，同时打开只读闸门——

- 执行器在权限规则、Hooks、审批之前硬拒一切非只读工具（`write_file`、`task` 等），错误信息会提示按 `Shift+Tab` 切回默认模式再执行
- `bash` / `execute_command` 按命令级判定参与该闸门：判定为只读的命令（`git status`、`ls` 等）放行，写操作命令仍被硬拒
- 只读工具（`read_file`、`grep`、`glob_files` 等）正常放行，其上的 deny 权限规则依然生效
- 被拒绝的调用不产生审批决策、不写审计、不触发 Hooks

典型用法：先切到 plan 态让 Agent 只读地调研并给出方案，审阅满意后再切回 Default 让它落地执行。

### 与 `--mode plan`、`/plan` 的区别

Shift+Tab 的 `plan (read-only)` 是**交互 REPL 的会话权限模式**；另外两套名字带「plan」的机制语义不同，别混用：

- `nexuscli --mode plan -p "..."`（单次模式）：Plan-and-Execute 代理做只读规划——只读闸门全程开启，不会执行任何写入；`--yes` 在该模式无效（打印 warning 并忽略），被闸门拦下的计划按失败收场（exit 1；`--fail-on-blocked` 时 exit 2，`--json` 的 `status` 为 `blocked`）。
- REPL `/plan <task>`（交互式 Plan-and-Execute）：与 `--mode plan` 同一只读闸门口径，在交互会话里流式执行，支持 checkpoint 与 `--resume` 续跑。

两者的 checkpoint 与续跑口径见 [plan-execute.md](plan-execute.md)。
