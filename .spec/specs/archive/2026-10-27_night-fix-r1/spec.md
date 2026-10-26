# 夜间②高优缺陷修复第一轮 - 项目范围

<!-- spec-format: 2 -->

## 1. 问题定义
- **项目目标**：修复夜间① findings.md 中的 P0-1/P1-1（MCP HTTP 无鉴权 + 绕过执行链）、P1-5（Auto 与写域守卫矛盾）、P1-3（REPL /plan 可写口径不一致）、P1-2（UserPromptSubmit/Stop hooks 只护 react 路径），每项先有回归测试再修
- **目标用户**：NexusCLI 真实用户（MCP 服务器部署者、REPL Auto 使用者、/plan 与 hooks 用户）
- **核心价值**：守卫跨路径一致——不再有"某条入口绕过审批/审计/只读闸门"的旁路

## 2. 假设与待确认

### 2.1 已确认事实
- findings.md（.spec/specs/archive/2026-10-27_night-verify-baseline/）各项均有 path:line 证据，P0/P1-5 已由主会话或核查员实测复现
- executor.py:153 从 config.policy.audit_log_path 构造 AuditLog——MCP 路径改走 executor 即自动获得审计
- ToolExecutor(registry) 构造后经 execute_all(calls, context) 分发，门序：未知工具→plan 只读→permission 规则→hooks→HITL→执行

### 2.2 关键假设
- MCP HTTP 增加必填 API key（NEXUSCLI_MCP_API_KEY）是可接受的破坏性变更：该入口原本就能无审计跑 bash，安全修复优先于兼容
- MCP 服务器保留 hitl_mode=never 语义（无人值守），由鉴权与审计补齐安全面
- Stop hook 对 plan/team 只是通知（blocked 不强制续跑），与 react 语义一致

### 2.3 待确认问题
- [x] test_mcp_server_protocol / test_lane_e_mcp_sdk 是否经 HTTP 路径受鉴权影响（执行时确认，stdio 不受影响）

### 2.4 可选解释与取舍
- 当前选择：写域守卫以 command_guard_enabled=False 判定 full-access（而非新增标志位）-> Auto 模式本就同时关 command guard，语义同源，不加新配置面
- 当前选择：hooks 门在两个 runner 各自实现而非统一入口分发 -> 改动面小；agent.py 的 react 门保持原位避免 SDK 语义漂移

### 2.5 已证伪路径
- 无（暂无证伪记录）

## 3. 功能范围

### 3.1 核心功能（MVP）
- [x] P0-1+P1-1：MCP HTTP 必须配置 NEXUSCLI_MCP_API_KEY 否则拒绝启动；请求带 Bearer/x-api-key 鉴权（401 fail-closed）；tools/call 改走 ToolExecutor 全链（审计/hooks/规则/写域守卫生效）；顺带修 serve_stdio 的 stdin EOF 崩溃（P2-18）
- [x] P1-5：写域守卫在 command_guard_enabled=False（full-access）时不再重复问/拒绝；无回调 fail-closed 保持
- [x] P1-3：REPL /plan 用 per-run 配置副本打开 plan_mode 只读闸门，会话 config 不污染
- [x] P1-2：UserPromptSubmit 门与 Stop 通知下沉到 PlanExecuteAgent.run 与 AgentOrchestrator.run；agent.py react 门收窄到 react 防双触发
- [x] 全量回归通过

### 3.2 扩展功能
- 无

### 3.3 不在范围内
- 其余 P2（留给第③棒）、文档同步（第⑤棒统一做）、workbench 前端

## 4. 最小实现路径
- 最简单可行方案：MCP 服务器内改用 ToolExecutor + 环境变量鉴权；builtins.py 一个条件分支；repl.py deepcopy 配置；两个 runner 顶部插同款 gate 代码块（不做共享抽象——三处 12 行以内，抽象无第二实现者）
- 暂不引入：MCP token 轮换、审计远程投递、hooks 配置格式变更
- 不做的抽象/配置化：不建 config 项控制 MCP 鉴权开关（fail-closed 不给关闭选项）

## 5. 技术决策
- 技术栈：stdlib http.server + 既有 ToolExecutor/hooks 机制
- 证据协议：evidence: runs
- 本轮允许改动：src/nexuscli/mcp/server.py、src/nexuscli/tools/builtins.py、src/nexuscli/entrypoints/repl.py、src/nexuscli/agent/{agent,plan_execute,orchestrator}.py、tests/{test_mcp_http_security,test_fs_scope,test_lane_c_modes,test_hooks*}.py、README.md 相关行
- 本轮不应触碰：frontend/、memory/、runtime/tasks、context/
- Git integration branch：`spec/2026-10-27_night-fix-r1`

### 5.1 验证策略
- 范围级别：integration
- 变更对象：MCP 服务器安全面、REPL 审批语义、plan/team 入口守卫
- 快速检查：`uv run --extra dev python -m pytest tests/test_mcp_http_security.py tests/test_fs_scope.py tests/test_lane_c_modes.py tests/test_hooks.py -q`
- 集成检查：task_0005 全量 pytest（三条路径共享 executor/hooks，必须全绿）
- 全项目检查：仅在收尾或升级触发时执行——task_0005 全量 pytest + ruff + uv build（本轮收尾即执行；打包载荷含 mcp/server.py，故 build 必跑）
- 升级触发：全量回归暴露跨路径行为漂移，或守卫改动影响 README 承诺的语义时，回写本包并本轮修复

## 6. 成功标准与验证方式
- [x] 无 key 的 MCP HTTP 请求被 401 拒绝且无法执行工具；缺 key 拒绝启动 -> verify: task_0001
- [x] MCP tools/call 经 executor：审计行落盘、hooks 门生效 -> verify: task_0001
- [x] full-access（command guard 关）下写域守卫不再拦 auto 模式；无回调 fail-closed 不变 -> verify: task_0002
- [x] REPL /plan 的配置副本 plan_mode=True 且会话 config 不被改 -> verify: task_0003
- [x] plan/team 入口的 UserPromptSubmit 阻断与 react 同效 -> verify: task_0004
- [x] 全量 pytest + ruff + uv build 通过 -> verify: task_0005

## 7. 风险与约束
- 风险点：MCP 鉴权破坏现有用户工作流 -> 缓解：错误信息带设置指引；README/docs 同步（第⑤棒复核）
- 风险点：hooks 双触发（SDK Agent.run plan/team）-> 缓解：react 门收窄 + 测试断言单次触发
- 风险点：full-access 判定误伤 headless never -> 缓解：test_fs_scope 既有 never 用例保持全绿
