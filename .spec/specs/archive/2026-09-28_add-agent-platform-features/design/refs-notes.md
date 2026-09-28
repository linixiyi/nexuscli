# 参考实现笔记（OpenCode + Claude Code）

参考源（只读，勿改）：
- OpenCode 官方仓库克隆：`E:\Code\.refs\opencode`（核心包 `packages/opencode/src/`）
- Claude Code 社区 TS 重实现：`E:\Code\.refs\claude-code\claude-code`（`src/`）

## 1 权限规则
- opencode `packages/opencode/src/permission/index.ts:28-38,186-198`：规则集为扁平 `[{permission, pattern, action}]`；config 形如 `permission: {"bash": "allow", "edit": {"src/**": "ask"}}`；`evaluate()` 用 `findLast`（最后匹配优先）；无规则命中时默认 `ask`；运行期 "always" 批准追加到内存规则集（:145-151）；完全 deny 的工具直接从工具列表隐藏（:204-214，nexusCLI 不采纳隐藏）。
- claude-code：allow/deny/ask 规则列表 + `Tool(spec)` 语法，deny 优先，交互式 "always allow" 持久化为规则。
- **nexusCLI 已定且已实现**（`policy/permission_rules.py`）：单一 `tool(pattern)` 语法，deny > ask > allow 优先级（与 opencode 的 last-wins 不同，保持现实现）。

## 2 hooks
- claude-code 引擎：`src/utils/hooks.ts`（协议核心 :421-433 schema、:498-624 stdout JSON 解析、:747+ command 执行、:877 timeout）与 `src/services/tools/toolHooks.ts`（PreToolUse/PostToolUse 接入 :39-193、permission 交互 :322-433）。
- 配置结构：`hooks -> <EventName> -> [{matcher?, hooks: [{type: "command", command, timeout?}]}]`。nexusCLI 放 config.json `hooks` 键，分层合并用**列表拼接**（同 permissions 语义，user 在前 project 在后）。
- 事件（本轮五个）：`SessionStart` / `UserPromptSubmit` / `PreToolUse` / `PostToolUse` / `Stop`。
- matcher：正则字符串，匹配**工具名**（仅 PreToolUse/PostToolUse）；缺省或 `*` = 匹配全部。
- 子进程协议：hook 命令 `shell=True` 执行，stdin 收 JSON 载荷（含 `hook_event_name`、`session_id`、`cwd`、`tool_name`、`tool_input`、`tool_response` 等，按事件裁剪）；**exit 0** = 正常（stdout 若为 JSON 则解析决策）；**exit 2** = 阻断（stderr 作为阻断原因回给模型）；其他 exit = 非阻塞错误（stderr 提示用户，继续执行）；超时（默认 60 秒，`timeout` 字段秒数）= 非阻断错误。
- stdout JSON：顶层 `{"decision": "approve"|"block", "reason": str}`；PreToolUse 另支持 `{"hookSpecificOutput": {"permissionDecision": "allow"|"deny"|"ask", "permissionDecisionReason": str}}`。
- 关键差异（nexusCLI spec §7 已定）：**PreToolUse 的 hook allow 不绕过 requires_approval 审批**，只允许 deny/block 与附加上下文；UserPromptSubmit block = 拒绝该条用户输入（提示原因）；Stop 的 block 仅提示不强制续跑。
- opencode 对应物是 plugin 系统（`src/plugin/index.ts:255-262`，event 回调收 `{id,type,properties}`），本轮不采纳。

## 3 子代理 task 工具
- opencode `packages/opencode/src/tool/task.ts:43-62`：参数 `{description(3-5词), prompt, subagent_type}`（task_id/background 本轮不采纳）；深度防护 `depth >= subagent_depth(默认1)` 直接报错（:104-117）；子会话默认追加 `deny task/todowrite` 防递归（:143-155，`agent/subagent-permissions.ts`）；结果取最后一条 assistant 文本（:224）。
- opencode agent 定义（`src/agent/agent.ts:33-49`）：name/description/mode/permission/model/prompt 等字段；内置 explore 提示词（`src/agent/prompt/explore.txt`）：只读搜索专家——绝不创建文件、绝不跑改系统状态的命令、返回绝对路径。
- claude-code `src/tools/AgentTool/loadAgentsDir.ts:75-122`：自定义代理 frontmatter 必填 name/description，可选 tools 等；无 name 字段的文件跳过。
- nexusCLI 已定：不做 per-agent model 路由（frontmatter 预留但不实现）、不做后台/并行 fan-out；深度 1 防递归；子代理走同一 ToolExecutor 与 policy（审批回调透传）。

## 4 slash 命令增强
- claude-code：命令 markdown frontmatter `description` / `allowed-tools` / `argument-hint`；`$1..$9` 位置参数替换；`` !`cmd` `` 展开为命令输出。
- opencode `packages/opencode/src/command/index.ts:22-44`：Info {name, description, agent?, subtask?, hints}；`hints()` = 模板中出现的 `$N` 与 `$ARGUMENTS`（提示用）；无 shell 注入语法。
- nexusCLI 已定：`mode`/`allowed-tools`/`argument-hint` + `$1..$9` + `` !`cmd` ``（注入命令先过 `classify_command`，危险类直接拒绝并在展开结果中注明）。

## 5 /compact
- claude-code `src/commands/compact/compact.ts:27-60`：`/compact [instructions...]` 附加自定义指示；压缩后向用户报告统计。
- opencode `src/agent/prompt/compaction.txt`：结构化摘要提示词，"Do not continue the conversation. … Respond in the same language as the conversation."
- nexusCLI 已定：ContextWindowManager 为**确定性抽取式压缩（不调 LLM）**——/compact 复用它；`focus` 作为摘要保留指示行注入；报告压缩前后 token 与被摘要条数。

## 6 plan 模式
- claude-code：Shift+Tab 三态循环 default → auto-accept → plan；plan 态在权限网关硬拦截写操作（只读工具放行）；ExitPlanMode 工具请求用户确认后退出（`src/tools/ExitPlanModeTool/ExitPlanModeV2Tool.ts:147-206`）。
- opencode：plan 是内置 agent（工具集在 agent 定义中过滤），非会话态。
- nexusCLI 已定：**executor 层硬拒绝非只读工具**（可测试）；三态 `default→auto→plan→default`；不做 ExitPlanMode 工具（再按 Shift+Tab 即退）。
