# NexusCLI 评审残留修复 - 任务拆解

## 使用规则

- 每个任务只改 `boundary` 列出的文件；发现相邻问题先记录，不顺手修。
- 机器任务的 `verify` 以首个反引号 span 为执行命令；命令必须在仓库根目录可执行。
- 两个任务承接自 2026-11-07_fix-nexuscli-release-hardening 收尾轮 M1 修复时明确挂账的两项已知残留；前置机制（gate_denial 转发、`_team_result_from_events`、SSE seq）已在该包完成并合入 main。

## 阶段 A：残留修复（机器任务）

- [ ] 0001 plan worker 的嵌套 plan_failed 转发到外层事件流（原挂账一）
  - boundary: `src/nexuscli/agent/orchestrator.py`、`src/nexuscli/entrypoints/cli.py`、`tests/test_headless_status.py`、`tests/test_multi_agent.py`。
  - 行为：`--worker-mode plan` 的 `SubAgent._execute_plan` 折叠内层事件时，识别嵌套 `plan_failed` 并以结构化事件转发到外层流（镜像收尾轮 M1 对 gate_denial 的转发方式）；`cli.py` 的 `_team_result_from_events` 消费该事件——plan worker 失败时 team 结果报 `blocked`（带拒绝原因），不再静默 `ok`；react/plan 分支与既有默认行为不变。
  - 测试：`tests/test_headless_status.py` 补 team+plan-worker 失败用例（status blocked、blocked_reason、默认退出码 0、`--fail-on-blocked` exit 2）；`tests/test_multi_agent.py` 回归。
  - verify: `uv run pytest -q tests/test_headless_status.py tests/test_multi_agent.py`

- [ ] 0002 runtime 事件折叠放行 gate_denial 并在前端展示（原挂账二）
  - boundary: `src/nexuscli/runtime/api.py`、`frontend/src/derive.ts`、`frontend/dist/`（构建产物，src 变更后用 `npm --prefix frontend run build` 重建）、`tests/test_runtime_sse.py`、`frontend/e2e/workbench.spec.ts`（可选）。
  - 行为：`runtime/api.py` 的线程折叠白名单放行 `gate_denial` 事件（SSE 帧按既有规则带递增 `seq`），前端 `derive.ts` 保留该事件并在运行报告/审批区域展示被拒原因，与既有 approval_suspended/approval_resolved 事件顺序不冲突。
  - 测试：`tests/test_runtime_sse.py` 断言 gate_denial 帧下发且 seq 单调；e2e 可选补一条断言。
  - verify: `uv run pytest -q tests/test_runtime_sse.py tests/test_runtime_approvals.py && npm --prefix frontend run test:e2e`

---

**当前进度**：由脚本计算，无需手动维护
