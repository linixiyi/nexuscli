# nexusCLI 挂载点地图（2026-09-28 工作区现状，含 F1 未提交改动）

供各实现车道按图索骥；行号以当前工作区为准，若漂移按符号名定位。

## config.py（345 行）
- `_home() -> Path`（:12）：`return Path.home()`。**win32 注意**：`Path.home()` 优先 USERPROFILE，测试 monkeypatch HOME 无效。
- 分层加载 `load_config()`（:125-154）：默认 dataclass → user `~/.nexuscli/config.json` 深合并 → project `<root>/.nexuscli/config.json` 深合并（**此处已插 `_merge_permission_lists(data, user_config, project_config)`**，hook 列表拼接照抄此套路）→ `<root>/.env` → overrides → 环境变量 `_apply_env`。
- `_deep_merge`：dict 递归、**list 整体替换**（所以权限/hook 列表需要专门拼接函数）。
- `_dict_to_config`（:308 附近）：`XxxConfig(**_filter_known(data.get("xxx", {}), XxxConfig))` 逐节构造；新配置节照此注册。
- F1 已加：`PermissionsConfig(allow/deny/ask: list[str])`，挂在 `NexusCliConfig.permissions`（:120）。
- `PolicyConfig`（:83 附近）：`hitl_mode`（never/auto/always）、`audit_log_path`、`path_guard_enabled`、`command_guard_enabled`。

## policy/
- `audit_log.py`：`AuditLog(path).record(*, tool_name, input_data, outcome, approver, cwd)` 追加 JSONL（自动脱敏 SENSITIVE_KEYS）；`tail(limit)`。
- `command_guard.py`：`CommandGuard`；`tools/commands.py:75 classify_command(command) -> str` 返回危险类（safe/medium/high 类字符串）。
- `permission_rules.py`（134 行，F1 已完成）：`parse_rule(raw)`、`subject_for(tool_name, payload) -> (kind, value)`、`rule_matches(...)`、`evaluate_permissions(permissions, tool_name, payload) -> PermissionDecision | None`；`ACTION_PRECEDENCE = ("deny","ask","allow")`。

## tools/executor.py（203 行，F1 已接入权限规则）
- `ToolExecutor.execute_all(calls, context)`：只读并发（Semaphore=tools.max_concurrent_read）+ 其余串行。
- `_execute_single`（:49-136）调用链：validate → `evaluate_permissions` → deny 短路（audit outcome=deny, approver="permission-rule"）→ `_approval_decision(tool, data, context, permission)` → deny/skip 返回错误结果 → approver 判定 → `tool.execute` → 非 只读 audit。异常兜底 :123-136。
- `_approval_decision`（:138-171）：`permission.action=="allow"` 且 hitl_mode!="always" → approve；never 态 ask → deny（fail closed）；auto 态非 requires_approval → approve；否则 approval_callback（`{"tool_name","input","danger_level","description"}`，可同步可异步）。**F2 的 PreToolUse hook 与 force_prompt 插在这条链上，F6 的 plan 拒绝插在 _execute_single 进闸处。**

## tools/base.py（76 行）
- `ToolResult(content, is_error=False, display_summary=None, tool_use_id=None)`；`ToolDecision = Literal["approve","deny","skip"]`。
- `ToolContext(cwd, config, approval_callback=None, skill_context_buffer=None)` —— F3 加 `subagent_depth: int = 0`（带默认值；F2 的 session_id 走 `PolicyConfig.session_id` 运行时字段，不动本文件）。
- `Tool(name, description, parameters, handler, is_read_only=True, is_concurrency_safe=True, danger_level="safe", requires_approval=False, timeout=60.0, required_keys=[])`；模块级 `object_schema(properties, required)`。
- `registry.py`：`ToolRegistry.register/register_all/get/list_names/definitions`。

## tools/builtins.py（742 行）
- `get_builtin_tools() -> list[Tool]`：一串 `Tool(...)` 字面量；handler 均为 `async def _x(payload, context) -> ToolResult` 模块级函数。F3 的 task 工具照此模板追加。

## entrypoints/slash_commands.py（96 行，F4 主战场）
- `CustomCommand(name, description, body, source, path)`；`_parse_command_file`（:35-61）只解析 frontmatter 的 `description` 键（`text.split("---", 2)` + 逐行 `partition(":")`）。
- `load_slash_commands(cwd, home=None)`：user `~/.nexuscli/commands/` → project `<cwd>/.nexuscli/commands/`，project 同名覆盖。
- `split_command_message(message)`；`expand_custom_command(command, args)`（:91-96）：`$ARGUMENTS` 替换或尾接。

## entrypoints/repl.py（975 行）
- `SLASH_COMMANDS`（:40-75 附近列表）；`/help` 打印该列表 + 自定义命令（:380-384）。**F4/F5 要把 `/compact` 加进列表。**
- `PermissionMode = Literal["default","auto"]`（:78）；`PermissionModeController`（:82-107）：`set()` 切换时改写 `config.policy.hitl_mode/path_guard_enabled/command_guard_enabled`；`toggle()` 两态。**F6 扩三态。**
- `start_repl`（:118-257）：构造 console/registry/llm client/PromptAssembler/Agent(:153-160，approval_callback 闭包引用 permission_mode)/SessionStore/PromptSession(key_bindings=`_permission_key_bindings(permission_mode)`)；主循环 :220-257：`/` 开头 → 自定义命令 `_match_custom_command`（:264-276，expand 后 `_run_agent`）→ `_handle_slash`（:364+ if/elif 链）；普通消息 → `_run_agent`。**F2 的 SessionStart 触发点在进循环前；UserPromptSubmit 在 _run_agent 前（含自定义命令展开后）；F4 的 mode/allowed-tools 应用在 _match_custom_command 处；F5 的 /compact 是 _handle_slash 新 elif。**
- `_approval_prompt`（:808-827）：非 tty 直接 deny；y/n/a/s，a=切 auto。
- `_permission_mode_label`（:890）与 `_prompt_message`（:894+）渲染模式标签；样式类 `toolbar.mode.default/auto`（:212-213）。**F6 加 plan 态标签与样式。**
- `_permission_key_bindings`（文件尾部）：Shift+Tab 绑定 `permission_mode.toggle()`。

## context/manager.py（257 行）
- `ContextBudget(context_window, max_output_tokens, compression_threshold=0.8, compression_target=0.55, reserve_tokens=1024)`；`CompressionResult(messages, estimated_tokens_before, estimated_tokens_after, compressed, summarized_messages)`。
- `ContextWindowManager.prepare(messages, *, system_prompt, tool_definitions)`：超阈值或超条数才压缩；`_summarize` 确定性抽取式（不调 LLM）；`min_recent_messages` 近端保留。**F5 加公开 `compact_now(messages, *, focus="") -> CompressionResult`。**
- Agent 侧用法（agent/agent.py:200-234）：`ContextWindowManager(ContextBudget(context_window=client.max_context_window, max_output_tokens=config.llm.max_tokens, compression_threshold=config.memory.compression_threshold, ...))`；压缩后 yield `context_compressed` 事件。F5 的 /compact 在 repl 里照抄这个构造。

## agent/agent.py（496 行）
- `Agent(llm_client, tool_registry, config, cwd, approval_callback=None, mode="react", system_prompt=None, max_turns=20, ...)`（:59-102）；`self.history: list[Message]`、`self.skill_context_buffer`。
- `run(message)`（:108-150）：按 mode 分发 `_run_react/_run_plan/_run_team`；快照 pre/post-turn。**F2 的 Stop hook 触发点在 run() 的 finally 之后（或各 runner 返回前）；UserPromptSubmit 在消息进 runner 前。**
- `_run_react`（:167+）：skill 候选注入 → ToolContext（:181-186）→ 循环 {压缩(:224-233) → llm.chat → 工具调用 executor.execute_all}。

## tests/ 组织
- pytest 直跑，`uv run python -m pytest tests/test_xxx.py -q`；tmp_path + monkeypatch 是惯用件；fake 对象手写（无统一 conftest magic）；参照 `tests/test_permissions.py`（F1，242 行）与 `tests/test_executor*.py` 的 fake callback 写法。

## 风格与工具链
- Python 3.11+，仅标准库 + httpx/rich/prompt_toolkit/openai 兼容层；dataclass(slots=True) 惯例；模块 docstring 头（`"""x.py — 一句话"""`）；`from __future__ import annotations`。
- 门禁：`uv run python -m pytest -q`（全量 ~6 秒，~141 用例）、`uv run python -m ruff check .`、`uv run python -m ruff format --check .`。
- Mimosa 预提交钩子敏感点：凭据走 env 间接引用；SQL 用字面量或常量 f-string 习语；测试避免占位密钥真值形态。
