# F6 REPL plan 模式三态循环 — 实现简报（第三波）

必读：`design/nexuscli-context.md` repl/executor 节；`src/nexuscli/entrypoints/repl.py`（:78-107 PermissionModeController、:212-217 样式、:890-975 label/prompt_message/key_bindings）；`src/nexuscli/tools/executor.py`；`design/refs-notes.md` §6。

## 边界
只改 `src/nexuscli/config.py`（PolicyConfig 加一个 bool 字段）、`src/nexuscli/tools/executor.py`（plan 态拒绝）、`src/nexuscli/entrypoints/repl.py`（三态循环与显示）、`tests/test_plan_mode.py`（新建）。**开始时本文件集已含 F1/F2 波次改动，按现状增量修改，不重写他人代码。**

## 设计规格

### 1. 状态存储（config.py）
`PolicyConfig` 加 `plan_mode: bool = False`（默认关，旧配置零影响）。

### 2. executor 硬拒绝（tools/executor.py）
`_execute_single` 进闸处（validate 之前或之后均可，建议在权限规则评估之前、validate 之后）：`context.config.policy.plan_mode and not tool.is_read_only` → 直接返回错误 ToolResult（is_error=True），内容形如：
`Tool "write_file" is not allowed in plan mode. Only read-only tools are permitted. Press Shift+Tab to switch back to default mode and run the plan.`
不写审计（未发生审批决策）、不跑 hook 的 PostToolUse（F2 已接入时：PreToolUse hook 也不必跑——直接短路即可，docstring 说明）。task 工具（F3 注册，is_read_only=False）自动被拒，无需特判。

### 3. 三态循环（repl.py）
- `PermissionMode = Literal["default", "auto", "plan"]`。
- `PermissionModeController.set()`：
  - `default`：恢复 `_default_hitl_mode` 等三项（现状）+ `plan_mode=False`；
  - `auto`：现状（hitl=never、双 guard 关）+ `plan_mode=False`；
  - `plan`：**恢复默认 hitl/guard（同 default 分支逻辑）+ `plan_mode=True`**——plan 态不是免审批态，是只读约束态。
- `toggle()`：`default→auto→plan→default` 循环。
- `_permission_mode_label`：plan → "plan"（显示文案 `plan (read-only)`，对照 default/auto 现有风格）；样式类加 `"toolbar.mode.plan": "noreverse bold #38bdf8 bg:#000000"`；`_prompt_message` 的模式片段按三态渲染。
- `_permission_key_bindings`：Shift+Tab 仍调 `toggle()`（无需改逻辑），提示文案若含两态描述则更新为三态。
- `/hitl` 命令的别名表（:544-560 附近）保持 default/auto 可用；plan 只经 Shift+Tab 或（第四波）命令 mode 进入——若 `_hitl_command` 的 set 调用对 "plan" 无别名则不动。

### 4. 测试 `tests/test_plan_mode.py`
覆盖（executor 侧用真实 ToolExecutor + 内存 registry 注册 fake Tool；repl 侧只测 PermissionModeController 纯逻辑，不起 PromptSession）：
- plan 态 `write_file`/`bash`（fake 非 只读工具）被拒且错误信息含 "plan mode"；`read_file`/`grep`（只读）放行；
- plan 态下权限规则 deny 仍然生效（叠加顺序：先 plan 拒绝或先 deny 均可，断言结果为拒绝且原因可辨——按实现断言即可）；
- plan 态非 只读工具不触发 approval_callback（fake callback 断言未被调——被 plan 短路）；
- 三态循环顺序 default→auto→plan→default（连续 toggle 断言）；
- auto 态行为与现状一致（回归：hitl_mode 变 never、guard 关闭、plan_mode=False）；
- default 态 plan_mode=False（回归）。
