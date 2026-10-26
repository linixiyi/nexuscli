# 夜间②高优缺陷修复第一轮 - 任务拆解

## 使用规则
- 每个任务都要写清楚 `boundary` 和 `verify`；机器任务的 verify 以首个反引号 span 为执行的命令
- 如果一个任务没有验证方式，就不能开始；确无命令可执行的任务写 `verify: manual: <理由>`
- 发现的可执行问题必须回写本包，并在本轮做完
- verify 命令不得自引用本包 check 门禁（自指命令在「最近一条」语义下不可满足）

## 阶段一：实现
- [x] task_0001 MCP HTTP 鉴权 + 全链 executor（P0-1/P1-1，含 stdio EOF 小修）
  - boundary: 只改 src/nexuscli/mcp/server.py 与 tests/test_mcp_http_security.py；stdio 语义除 EOF 修复外不变
  - verify: `uv run --extra dev python -m pytest tests/test_mcp_http_security.py tests/test_mcp_server_protocol.py tests/test_mcp.py tests/test_mcp_session_reuse.py tests/test_mcp_filter.py tests/test_lane_e_mcp_sdk.py -q; printf '\nMCP_EXIT=%s\n' "$?"`（运行目录 /Users/linyixi/nexuscli；成功判定：新增 401/拒启/审计用例全过且既有用例不回归）
- [x] task_0002 写域守卫尊重 full-access（P1-5）
  - boundary: 只改 src/nexuscli/tools/builtins.py 的 _reject_outside_write_scope 与 tests/test_fs_scope.py 新增用例
  - verify: `uv run --extra dev python -m pytest tests/test_fs_scope.py tests/test_write_scope_allow.py -q; printf '\nSCOPE_EXIT=%s\n' "$?"`（成功判定：新增 full-access 用例过，既有 never/headless fail-closed 用例保持全绿）
- [x] task_0003 REPL /plan 口径对齐（P1-3）
  - boundary: 只改 src/nexuscli/entrypoints/repl.py 的 /plan 分支与新增 _plan_run_config 辅助函数、tests/test_lane_c_modes.py 新增用例
  - verify: `uv run --extra dev python -m pytest tests/test_lane_c_modes.py -q; printf '\nLANE_EXIT=%s\n' "$?"`（成功判定：副本 plan_mode=True 且原 config 不变用例通过）
- [x] task_0004 hooks 下沉 plan/team（P1-2）
  - boundary: 只改 src/nexuscli/agent/{agent,plan_execute,orchestrator}.py 与新增 tests/test_hooks_lane_coverage.py
  - verify: `uv run --extra dev python -m pytest tests/test_hooks_lane_coverage.py tests/test_hooks.py tests/test_agent_turns.py tests/test_lane_c_team_task.py tests/test_worker_mode_pinned.py -q; printf '\nHOOK_EXIT=%s\n' "$?"`（成功判定：plan/team 阻断用例过；既有 hooks/回合用例不回归）
- [x] task_0005 全量回归
  - depends-on: task_0001, task_0002, task_0003, task_0004
  - boundary: 只读执行全量 pytest、ruff、uv build；失败回写本包本轮修复
  - verify: `uv run --extra dev python -m pytest -q; printf '\nPYTEST_EXIT=%s\n' "$?"; uv run --extra dev ruff check .; printf 'RUFF_EXIT=%s\n' "$?"; uv run --extra dev ruff format --check .; printf 'FMT_EXIT=%s\n' "$?"; uv build; printf 'BUILD_EXIT=%s\n' "$?"`（成功判定：四个 EXIT 真值齐全且为 0；非零回写本包修复后重跑）

---

**当前进度**：由脚本计算，无需手动维护
