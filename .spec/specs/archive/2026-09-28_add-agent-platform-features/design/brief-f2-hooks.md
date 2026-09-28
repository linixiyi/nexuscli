# F2 hooks 生命周期钩子系统 — 实现简报（第二波）

必读（先读再写代码）：
- `design/refs-notes.md` §2（协议规格，照此实现）
- `design/nexuscli-context.md`（挂载点地图）
- `src/nexuscli/config.py`（`PermissionsConfig` 与 `_merge_permission_lists` 是分层列表拼接的现成模板）
- `src/nexuscli/tools/executor.py`（现审批链，本波在你手里）

## 设计规格

### 1. 配置（config.py）
```python
@dataclass(slots=True)
class HookCommandConfig:
    type: str = "command"          # 本轮仅 "command"
    command: str = ""
    timeout: int = 60              # 秒

@dataclass(slots=True)
class HookMatcherConfig:
    matcher: str = "*"             # 正则，匹配工具名；仅工具事件用
    hooks: list[HookCommandConfig] = field(default_factory=list)

@dataclass(slots=True)
class HooksConfig:
    session_start: list[HookMatcherConfig] = field(default_factory=list)
    user_prompt_submit: list[HookMatcherConfig] = field(default_factory=list)
    pre_tool_use: list[HookMatcherConfig] = field(default_factory=list)
    post_tool_use: list[HookMatcherConfig] = field(default_factory=list)
    stop: list[HookMatcherConfig] = field(default_factory=list)
```
config.json 形如 `"hooks": {"PreToolUse": [{"matcher": "bash|write_file", "hooks": [{"type": "command", "command": "...", "timeout": 10}]}]}`。`_dict_to_config` 用 `_filter_known` 模式；事件键 camelCase（`SessionStart`/`UserPromptSubmit`/`PreToolUse`/`PostToolUse`/`Stop`）映射到蛇形字段。分层合并：user 在前、project 在后**列表拼接**（写 `_merge_hook_lists`，与 `_merge_permission_lists` 同套路，五事件循环）。

### 2. 新包 `src/nexuscli/hooks/`
- `__init__.py`：导出公共 API。
- `registry.py`：`hooks_for(config: HooksConfig, event: str, tool_name: str | None) -> list[HookCommandConfig]` — 事件键 → 字段映射 + matcher 正则匹配工具名（matcher 为 `*`/空/不含工具事件时全匹配；正则编译失败视为不匹配该 matcher，不抛异常）。
- `runner.py`：
  ```python
  @dataclass(slots=True)
  class HookOutcome:            # 多个 hook 聚合后的结果
      blocked: bool = False       # 有 exit 2 或 decision=block 或 permissionDecision=deny
      reason: str = ""            # 阻断原因（hook stderr 或 reason 字段）
      additional_context: str = ""  # UserPromptSubmit 的 additionalContext 追加
      errors: list[str] = []      # 非阻断错误（超时/其他 exit code），供提示
      permission_hint: str = ""   # "ask"（permissionDecision=ask → 强制 HITL）；"allow" 仅记录，绝不放行
  async def run_hooks(commands, event, payload) -> HookOutcome
  ```
  执行：`asyncio.create_subprocess_shell(cmd, stdin=PIPE, stdout=PIPE, stderr=PIPE, cwd=...)`；stdin 写 `json.dumps(payload, ensure_ascii=False)`；`asyncio.wait_for(..., timeout=h.timeout)`；超时 kill 归为 errors。exit 2 → blocked（reason 取 stderr，空则 "blocked by hook"）。exit 0 且 stdout 首个 JSON 对象可解析 → 处理 `decision`（approve/block+reason）与 `hookSpecificOutput.permissionDecision`（allow/deny/ask + permissionDecisionReason）。其余 exit → errors。载荷字段：`hook_event_name`、`session_id`、`cwd`、事件特定（`tool_name`、`tool_input`；PostToolUse 另有 `tool_response`；UserPromptSubmit 另有 `prompt`）。
  **红线**：`permission_hint == "allow"` 时**不得**绕过 requires_approval（executor 里只允许它跳过"无规则时 auto 态放行"之外什么都不做；实现上 allow 仅记录）。Windows 兼容：`create_subprocess_shell` 即可。

### 3. 接入点
- `tools/executor.py` `_execute_single`：权限 deny 判定之后、`_approval_decision` 之前跑 PreToolUse（blocked → 与 deny 同形的错误 ToolResult，approver 记 "hook"）；`permission_hint=="ask"` → 强制走 approval_callback（把 `_approval_decision` 加一个 `force_prompt: bool = False` 参数即可）；工具执行完成后跑 PostToolUse（结果 errors/blocked 仅打印不影响已产出结果；blocked 记 audit outcome="deny", approver="hook"）。未配置对应事件任何 hook 时**零开销**（先查列表长度，空直接短路，不进异步函数）。
- `agent/agent.py`：`run()` 开头（消息进入 `_run_react` 前）触发 UserPromptSubmit——blocked 则 yield 一个 error 事件并 return（不进 LLM）；循环正常结束后触发 Stop（blocked 仅 yield 提示性事件）。
- **session_id 通道（只经你的文件，勿动 tools/base.py——那是并行车道 F3 的领地）**：`PolicyConfig`（config.py）加运行时字段 `session_id: str = ""`（docstring 注明 runtime session stamp，与 hitl_mode 被控制器实时改写是同一模式）；`Agent.__init__` 里 `self.session_id = uuid4().hex[:12]` 并写 `self.config.policy.session_id = self.session_id`；executor 组 stdin 载荷时读 `context.config.policy.session_id`。
- `entrypoints/repl.py`：`start_repl` 在进入 while 循环前触发一次 SessionStart（未配置则跳过）；hook 的 errors/additional_context 用 `console.print` 呈现。
- 触发器封装成 `hooks/runner.py` 里的便捷协程（如 `fire_event(config, event, payload, cwd) -> HookOutcome`），调用方一行调用；agent/executor/repl 不直接拼 subprocess。

### 4. 测试 `tests/test_hooks.py`（用 subprocess 跑真 python -c 脚本，Windows 兼容；参考既有测试风格）
覆盖：matcher 精确/正则/`*`/不匹配；exit 2 阻断（executor 返回错误结果且含 stderr 原因）；stdout JSON `decision:block+reason` 生效；`permissionDecision:deny` 生效、`ask` 强制 HITL（fake approval_callback 断言被调）、`allow` **不**绕过 requires_approval（断言 callback 仍被调）；timeout（hook sleep 超过 timeout → errors 非阻断）；stdin 载荷字段断言（子进程把 stdin 写到临时文件再读回验证）；未配置 hooks 时 executor 行为与旧完全一致（回归）+ 零额外调用。
