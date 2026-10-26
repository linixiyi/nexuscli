# 夜间①功能全量验证基线 findings.md

生成时间：2026-10-27 01:35 左右（夜间接力第①棒）
核查方式：全量真实命令（pytest/ruff/build/CLI 冒烟/前端 e2e）+ 4 名只读核查子代理分片核查 8 个核心模块（workflow `夜间①核心模块并行核查`，run dwfrun-42a21f83），主会话抽查高严重度条目属实后录入。

## 一、验证结果总览（全部真实执行）

| 检查 | 结果 |
| --- | --- |
| pytest 全量 | **1345 passed, 2 skipped**（409s，退出码 0） |
| ruff check | All checks passed |
| ruff format --check | 265 files already formatted |
| uv build | sdist + wheel 成功（nexuscli-0.2.0） |
| CLI 冒烟 | `nexuscli 0.2.0`；doctor 全项正常（api_key configured, provider openai-compatible, model cbcn/glm-5.3-flash, python 3.13.13, node v24.13.1） |
| 前端 build | vite 成功（439.81 kB js / 19.40 kB css，2219 modules） |
| 前端 e2e | **11 passed (7.8s)**，含 U1/U2/U6/U7/W1/W2 UX 回归全部通过 |
| 模块核查 | 28 条发现：P0=1、P1=5、P2=22（详见下文） |

结论：测试基线健康，但**守卫在跨路径一致性上有系统性缺口**——多条守卫只护 ReAct 主路径，plan/team/MCP 旁路不受保护。

## 二、P0（1 条）

### P0-1 MCP HTTP 传输无鉴权 + 强制免审批 = 本地未授权任意命令执行
- 位置：`src/nexuscli/mcp/server.py:75-80`（hitl_mode 强制 never + 直调 tool.execute）、`server.py:154-167`（do_POST 无凭证检查）、`src/nexuscli/entrypoints/cli.py:420-436`（HTTP 入口已接线且 README:26 宣传）
- 现象：`nexuscli mcp serve --transport http` 后，本机任意进程可无凭证 POST /mcp 调 bash 等危险工具。**核查员已实测复现**：initialize + tools/call(bash, echo) 返回了命令输出；无审计、无审批、无 hooks。
- 证据链：executor.py:451 在 hitl_mode=="never" 时对任何工具（含 requires_approval=True）直接 approve；对照组 RuntimeApiServer 无 api_key 拒绝启动（api.py:289-292）、workbench 有一次性口令+CSRF，唯独 MCP HTTP 裸奔。
- 残余缓解（不足以定性为低危）：仅回环绑定、CommandGuard 在 bash 工具内仍生效、FSScopeGuard never 模式 fail-closed。
- 建议：HTTP 传输加凭证门（api_key 或一次性口令），未配置时拒绝启动，与 Runtime API 同款 fail-closed；stdio 不受影响。**同时修 P1-1**（同一处代码）。
- 预估：M（约 30-60 分钟，含测试）

## 三、P1（5 条）

### P1-1 MCP tools/call 直调 tool.execute，绕过 ToolExecutor 全链
- 位置：`src/nexuscli/mcp/server.py:66-88`
- 现象：审计日志、PreToolUse hooks、permission allow/ask 规则、工具 journal 全部不生效；与 -p 的 fail-closed 语义相反（executor.py:453-454 无回调即 deny，MCP 路径经 hitl_mode=never 直接放行）。违反 NEXUS.md「默认模式保留 HITL、工作区路径限制、命令守卫和审计日志」。
- 建议：MCP 路径改走 ToolExecutor（可保留 never 语义），至少补 audit.record 与 hooks 门。与 P0-1 一并修。
- 预估：M

### P1-2 UserPromptSubmit/Stop hooks 只在 ReAct 主循环触发，plan/team 全部公开入口绕过
- 位置：`src/nexuscli/agent/agent.py:212,245-246`；绕过点 `agent/plan_execute.py:71-131`、`agent/orchestrator.py:351-400`；入口 `repl.py:630-653,756-768`、`query_engine.py:64-74,101-111`、`runtime/api.py:1782-1789`
- 现象：agent.py 自称 "gate the message before it reaches any runner"，docs/hooks.md:28 承诺 exit 2 阻断——/plan 与 /team 路径该守卫完全不成立。
- 建议：把 UserPromptSubmit/Stop 门禁下沉进 PlanExecuteAgent.run 与 AgentOrchestrator.run，或让入口统一经 Agent.run 分发。
- 预估：M

### P1-3 同一 Plan-and-Execute 路径三个入口写行为不一致：REPL `/plan` 可写
- 位置：`src/nexuscli/entrypoints/repl.py:630-637`
- 现象：`-p --mode plan` 强制只读（cli.py:228-234 注释 "so the advertised guarantee holds"）、team 的 plan worker 强制只读（orchestrator.py:216-223），REPL `/plan <task>` 不设 plan_mode 可写文件。test_lane_c_modes.py:77-113 只覆盖 team worker。
- 建议：/plan 处理器对 per-run config 副本置 plan_mode=True（与 cli.py 同口径），或在 docs 明示 REPL /plan 是可写入口。**加测试锚定三个入口同口径。**
- 预估：S

### P1-4 `-p "/命令"` 只做文本展开，frontmatter `mode:`/`allowed-tools` 被静默丢弃
- 位置：`src/nexuscli/entrypoints/cli.py:264-281`（expand_custom_command 纯文本替换，slash_commands.py:299-304）
- 现象：REPL 侧 _run_custom_command 应用 mode/allowed-tools（custom_cmds.py:43-62），`-p` 侧不应用也不守卫；docs/slash-commands.md:26-29 无差别描述两种入口。带 `mode: plan`（只读审阅）或 `allowed-tools` 白名单的自定义命令在 -p 下语义反转。
- 建议：_run_prompt 消费 expansion.mode/allowed_tools，或在 docs 明确 frontmatter 仅 REPL 生效（二选一，倾向前者，同口径）。
- 预估：M

### P1-5 REPL「Auto (full access)」与写域守卫互相矛盾
- 位置：`src/nexuscli/tools/builtins.py:697-702`（hitl_mode==never 时写域守卫无条件 fail-closed）× `src/nexuscli/entrypoints/repl.py:142-146`（Auto 模式把 hitl_mode 置 never 但审批回调仍在）
- 现象：Auto 模式下 executor 对所有工具自动放行（executor.py:451）、path_guard 关闭可写任意位置，唯独写域守卫拒绝动用明明存在的审批回调——bash 域外写被拒、write_file 却成功，三条路径对同一约定互相矛盾。README:176 承诺 Auto 为「免审批 + 关闭守卫」。核查员实跑复现（bash 域外写被拒回调 0 次问，write_file 成功）。test_fs_scope.py:491 只钉了 headless never。
- 建议：写域守卫与其余守卫同源——检查 path_guard_enabled/command_guard_enabled 或由 PermissionModeController 设置专门的 full-access 标志；fail-closed 保留给真正无回调的运行。补 REPL Auto 组合的测试。
- 预估：S-M

## 四、P2（22 条，按模块分组）

### agent / entrypoints
1. `agent/plan_execute.py:57` — use_journal=True 创建的 _journal 从未传入 ToolContext，plan worker 不记工具日志；orchestrator 连参数都没有；repl.py:636 传的 use_journal=True 是无效参数。建议完成接线或删死参数。
2. `agent/query.py:126-133` — F12 max_tokens 守卫（合成 is_error、不执行降级输入）只在 react；plan/team worker 把截断调用交给执行器。建议提取共享 helper。
3. `agent/query.py:75` — LoopGuard 只护 react；plan/team worker 无循环检测，SDK 路径 max_task_turns=200 敞口大。建议下沉到 query() 或 docs 注明范围。
4. `agent/agent.py:202` — SDK Agent.run(mode='plan'/'team') 内外双层各建 SnapshotService，一轮落 4 份快照记录（仓库内无调用方，仅 SDK 暴露）。建议内层快照可注入/可关闭。
5. `entrypoints/repl.py:967` — _record_turn_usage 注释清单与实现不符：/team 与 /turn-resume 同样不记录，/team 消耗 token 不进 usage.db。建议补记录或修注释。
6. `entrypoints/repl.py:89-121` — /turn-resume 已实现且有 docs 专节，但不在 SLASH_COMMANDS：/help 不列、Tab 补全不出、docs 命令总表也漏。建议加入清单。
7. `render/rich_renderer.py:473` — _preserve_line_breaks 与 mid-turn SIGINT 中止均零测试（AGENTS.md 两条守则无自动化回归）。建议补单测与 pty 用例。

### tools / policy
8. `policy/audit_log.py:18` — token 掩码只覆盖 sk-/gh[pousr]_/AKIA，bash command 里的 `Authorization: Bearer <JWT>` 原样落盘（核查员实测复现）。建议增加值级掩码。
9. `entrypoints/cli.py:655` — --yes 自动放行在审计记 approver="hitl"，与真人审批不可区分（cli.py 自认）。建议独立 approver 值（yes-flag）。
10. `policy/permission_rules.py:109` — 'prefix:*' 用 startswith 无词边界：allow "bash(git diff:*)" 会放行 `git difftool`（实跑复现）。建议边界检查或文档明示。
11. `entrypoints/commands/approval_keys.py:13` — 共享 APPROVAL_HELP 宣传 "approve this tool for the rest of this run"，但写域守卫的 r 实际只授权单次——文案与行为相反。建议守卫专属帮助行。
12. `policy/bash_readonly.py:49` — printenv/裸 env 免审批直通，安全性完全依赖 env_sanitization 默认开启；关闭该开关则环境变量免审批入转录。建议 sandbox 关闭时收回直通或文档标注。
13. `tools/executor.py:354` — AuditLog.record 写盘无容错且在 try 内：审计写失败会把已成功的副作用调用翻转为 error + journal 标 outcome_unknown，except 内第二次 record 可能再抛导致 execute_all 崩溃（代码推演，未故障注入）。建议 best-effort 化。

### memory / context / usage
14. `memory/manager.py:236-238` — recall() 空白 query 提前回落 list() 时静默丢弃 kinds 过滤器（实测复现：空白 query + kinds=["preference"] 返回了 fact 行）。建议给 list 加 kinds 或走评分路径。
15. `prompt/assembler.py:22` × `tools/builtins.py:894` — 记忆 scope 口径不一致：assembler 用 resolve() 后 cwd，save/search 工具用原始 context.cwd；SDK 直构传相对 cwd 时存取两侧 scope 不等、保存的记忆对自动召回不可见（实测复现）。建议 MemoryManager 构造侧统一 resolve。
16. `llm/usage_store.py:29` — turn_usage 表无任何清理/保留路径，usage.db 无界增长（docstring 自述 intentionally not ported）。建议补保留窗口清理或 prune 子命令。
17. `tools/journal.py:119` — ToolJournal._connect 是全仓唯一不带 timeout/busy_timeout 的 SQLite 连接（兄弟库都 timeout=30+busy_timeout=30000）；/team 并行共享单例时锁竞争窗口窄，超 5s 直接 OperationalError。建议对齐统一连接形态。

### runtime / mcp / render
18. `mcp/server.py:96` — serve_stdio 在 stdin EOF 时未捕获 EOFError 栈崩溃（input() 在 try 块外，实测复现），而非设计的静默 break。建议移入 try。
19. `runtime/tasks.py:101` — 任务无最大重试上限：attempts 只增不比，worker 硬死亡（kill -9/OOM）的任务被租约恢复无限重派，毒任务永久占槽。建议加最大 attempts，超限转 failed。
20. `runtime/api.py:702` — Runtime API 每 turn/task 重建 McpClientManager 并丢弃旧实例：MCP 会话跨 turn 不复用（有状态 server 每 turn 丢状态 + 冷启动；AGENTS.md「client 会话要复用」在跨 turn 边界失守）。建议 manager 提升到 RuntimeApiServer 生命周期。
21. `mcp/auth.py:190-205` — TokenStore.save 用 write_text 后置 chmod 0600 且 OSError 被 suppress（OAuth token 文件）：write-then-chmod 弱权限窗口，与 api.py:337 已修复的同款问题（复审 F7）跨路径不一致。建议 os.open(..., 0o600) 直接创建。
22. `render/plain.py:7` — PlainRenderer 是未接线死代码：entrypoints 与测试零引用，config.render_mode="plain" 无消费方。违反 NEXUS.md「不能只依据未接线的内部类宣称支持」。建议删除或真实接线。

## 五、验证通过项汇总（不需要第②③棒重复核查）

- 三条执行路径共享同一 ToolExecutor/守卫链/记忆召回/Skill 注入/快照门控；prompt_toolkit SIGINT 按 AGENTS.md 落实（repl.py:318-326,983-1010）；rich.Markdown 硬换行实测正确且 -p 路径不受影响（cli.py:686 强制 plain）。
- 审批矩阵全部 requires_approval=True（task 子代理委派除外，属设计且子代理内部独立把关）；-p 无回调 fail-closed 实测成立；写域 allow 语义/hitl always/approve_run 单次/2>/dev/null 全部符合 AGENTS.md；只读命令碰凭证路径 17 条用例全部落回审批链；审计覆盖完整、脱敏按键名+token 模式、文件 0600；工具 journal fail-closed 四分支。
- 压缩/裁剪摘要全部 user 角色（manager.py:213-241 + 测试断言）；多信号时态召回无缺陷；记忆 scope SQL 全带过滤、SQLite 并发 4 进程×50 写实测通过、清理/配额/迁移完备；Runtime 任务四语义（作用域/原子领取/租约恢复/迟到结果不覆盖）全过 + 9 测试；MCP server initialize 三要素全（官方 SDK 握手回归过）、client 会话 turn 内复用；进程组 start_new_session+killpg+清空才报 stopped 实测。
- 勘误：会话存储是 JSONL 非 SQLite（本仓 SQLite 共五处：memory.db、usage.db、tasks.db、journal.db、rag/code_index）。

## 六、遗留清单与第②③棒建议

- **第②棒（P0/P1 修复）建议顺序**：P0-1+P1-1（MCP 同一处，一起修，加未授权拒绝的回归测试）→ P1-5（Auto 矛盾）→ P1-3（/plan 口径）→ P1-2（hooks 下沉）→ P1-4（-p frontmatter）。每项先写复现/回归测试。P2 里第 8（JWT 落盘）与第 19（任务无限重派）若时间富余值得提前。
- **第③棒（升级）候选**：P2 第 16（usage 保留窗口）、第 20（MCP 会话跨 turn 复用）、第 6（/turn-resume 入清单）、第 1（journal 接线 F10 悬空）、workbench UX 第三轮（e2e 里 U/W 系列已稳定，可做视觉层）。
- 本包不修任何代码；以上全部为带证据的待办。
