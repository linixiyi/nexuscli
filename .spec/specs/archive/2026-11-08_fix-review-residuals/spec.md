# NexusCLI 评审残留修复 - 项目范围

<!-- spec-format: 2 -->

## 1. 问题定义

- **项目目标**：修复已归档包 2026-11-07_fix-nexuscli-release-hardening 收尾轮 M1 修复时明确挂账的两项已知残留——① plan worker 的嵌套 `plan_failed` 事件被 `SubAgent` 内层丢弃；② runtime 事件折叠忽略 `gate_denial`，workbench 不展示门拒绝。
- **目标用户**：在 zcode 中执行本包的 AI 代理，以及 NexusCLI 维护者。
- **核心价值**：补齐 headless 与 workbench 两条用户可见路径上「被拦截但静默成功/不可见」的最后两处缺口。
- **Literal ask**: 「你拉起工作流把剩的能做的做了」（2026-11-08，用户指令清掉剩余可做项）。
- **Stated purpose**: 让 plan worker 失败与门拒绝在 team/headless 与 workbench 路径上如实可见。
- **Inferred outcome**: 两类失败在各自用户路径上有 blocked/原因展示，测试锁定；confidence: high; evidence: 收尾轮 M1 修复记录（运行 dwfrun-1b24d3de）。
- **Constraints and exclusions**: 只补事件可见性，不重写编排/前端架构；不新增配置项；不改变既有默认行为与 fail-closed 语义。
- **Trade-off preference**: correctness + observability 优先；最小接线。

## 2. 假设与待确认

### 2.1 已确认事实

- M1 修复已落地 `gate_denial` 转发（AgentMessage 字段、`_team_result_from_events`、SSE seq 机制）——本包只复用既有机制。
- 两项残留的位置与修法已在收尾轮复核记录中写明（orchestrator.py:189-200/225、470-474；runtime/api.py 线程折叠白名单；derive.ts）。

### 2.3 待确认问题

- [x] 为何现在才修？→ 收尾轮按最小接线原则挂账；用户 2026-11-08 指令清掉剩余可做项。

### 2.2 关键假设

- A1 | gate_denial 转发与 `_team_result_from_events` 机制已在 main 可复用 | system | 收尾轮 M1 修复记录与 main HEAD 代码实读 | CONFIRMED | if false: 本包退化为重新实现转发机制，工作量加倍
- A2 | 两项修复不影响 repl 等其他外层事件消费方 | system | 外层事件流消费方仅 cli 与 runtime/api，均有 scoped 回归覆盖 | UNVERIFIED | if false: 按 regression 结果扩大 boundary 并记录偏离

## 3. 功能范围

### 3.1 核心功能（MVP）

- plan worker 失败在 team/headless 路径报 blocked（原挂账一）。
- gate_denial 在 workbench/SSE 路径可见（原挂账二）。

### 3.3 不在范围内

- 不处理真实模型 eval、Windows 沙箱等 3.2 扩展功能；不做事件溯源式重写。

## 4. 最小实现路径

- 最简单可行方案：两任务并行——0001 复用收尾轮 M1 的 gate_denial 转发模式补 plan_failed 转发；0002 在 runtime 折叠白名单放行 gate_denial 并在 derive.ts 展示。
- 暂不引入：事件溯源、前端状态管理库、新的配置项。
- 不做的抽象：不为单一事件类型建通用转发注册表（单一实现者阶段保持直连）。

### 4.1 方案阶梯

- B0 | literal ask | 两任务最小接线修复，覆盖 headless 与 workbench 两条用户可见路径 | 用户指令清掉剩余可做项；两项残留的位置与修法已在收尾轮复核记录写明
- B1 | minimal change | 只修 headless 路径（0001），workbench 展示留待后续 | 交付更快，但 workbench 用户仍看不到门拒绝，不满足「清掉可做项」的字面范围
- B2 | recommended alternative | 两项都修且顺带把事件折叠重写为注册表 | 覆盖面相同但引入不必要的重构，违反最小接线取舍

Recommendation: B0 because 两项残留的机制前提已验证存在，最小接线即可闭环。Flips if A2 is FALSIFIED.

## 5. 技术决策

- 证据协议：evidence: runs（每个机器 verify 必须由 spec_verify.py 真实执行并记录）
- 本轮允许改动：仅 tasks.md 中每个任务 boundary 列出的文件。
- 本轮不应触碰：AGPL 许可证、telemetry、无关模块、生成物（frontend/dist 由构建产生）。
- Git integration branch：`spec/2026-11-08_fix-review-residuals`

### 5.1 验证策略

- 范围级别：package
- 变更对象：orchestrator/cli 事件流与 runtime/api 折叠白名单、derive.ts 展示。
- 快速检查：`uv run pytest -q tests/test_headless_status.py tests/test_multi_agent.py tests/test_runtime_sse.py tests/test_runtime_approvals.py`
- 集成检查：`npm --prefix frontend run test:e2e`
- 全项目检查：适用外（两任务边界清晰，不触共享基础设施；若 package 检查暴露跨边界回归再升级并记录理由）。
- 升级触发：只有 package/integration 检查暴露跨边界回归时，才升级为全量 `uv run pytest -q` 并记录理由。

<!-- spec:correction-log -->
### 5.5 修正日志

- none.

<!-- spec:deviation-log -->
### 5.6 偏离日志

- none.

## 6. 成功标准与验证方式

- [x] `--worker-mode plan` 的 team 运行中 plan worker 失败时报 `blocked` 与原因，headless 不再静默 `ok` -> verify: task_0001
- [x] workbench/SSE 路径可见 `gate_denial`（帧带 seq、前端展示被拒原因） -> verify: task_0002

## 7. 风险与约束

- 风险点：外层事件流新增事件类型可能影响既有消费方（runtime/api、repl）。→ 缓解：只加不改，回归跑 multi_agent 与 runtime_sse 全套。
- 风险点：SSE seq 单调性破坏前端游标。→ 缓解：复用收尾轮已验证的 seq 机制，测试断言 seq 单调。
