# 对齐第 3 轮：审批异步化（A9） - 项目范围

<!-- spec-format: 2 -->

## 1. 问题定义
- **项目目标**：接通 approval_pending 生产者（F11 留下的最大残留差距）——把工作台审批从
  「同步阻塞回调」升级为「挂起即 checkpoint」：审批挂起时 run 挂起（RunSuspension）、
  状态落 turn_checkpoint、决策回传（Web/重启恢复）后按 journal 语义恢复执行
- **目标用户**：长任务用户（审批等待期间进程可退出、可重启恢复）
- **核心价值**：审批等待不再占死执行线程；kitagent interrupt 语义（挂起即 checkpoint、
  恢复先验 revision、input_hash 防参数漂移）完整落地

## 2. 假设与待确认

### 2.1 已确认事实
- 契约来源：差距矩阵 high 的 A9 行 + 05 文档 F11 的三态定稿（approval_pending 本轮挂账的原因
  恰是本轮要消除的）+ 05 文档 F10 的 turn_checkpoint 设施（已落地）
- kitagent 参照：core/graph/interrupts.py:20-113（PermissionInterruptPayload 含 input_hash；
  interrupt() 挂起即 checkpoint；恢复先验 revision）、graph/engine.py:207-259（异常收尾合成结果）
- 现状：F6 同步阻塞回调（event.wait 300s）；F15 后 Web 决策走 POST decision → decide() 唤醒
- 依赖设施已就位：turn_checkpoint（双快照点）、journal（原 id 重派）、EngineResult sum type

## 2.2 关键假设
- 同步阻塞回调（REPL 路径）保留不动；异步挂起是 **Runtime API 任务/工作台路径的新增模式**
  （mode 可选），两条路径并存
- 挂起期间任务保持 running（心跳保活 lease——F6 已验证该机制）

### 2.3 待确认问题
- 无

### 2.4 可选解释与取舍
- 当前选择：挂起模型仅接入工作台/Runtime API 任务路径（REPL 同步阻塞体验不变）-> 改造半径收窄

### 2.5 已证伪路径
- 无（暂无证伪记录）

## 3. 功能范围

### 3.1 核心功能（MVP）
- [x] A9 审批异步化：TaskRecord/任务执行路径的审批挂起 → RunSuspension(approval_pending) +
  turn_checkpoint 落挂起态（pending 调用 + approval ref + revision）→ 决策回传恢复执行
  （approve → 重派原调用经 journal；deny → 合成 deny 结果）→ EngineResult 折叠层透出挂起

### 3.2 扩展功能
- 无

### 3.3 不在范围内
- REPL 同步阻塞回调改造（保持现状）；SSE 审批推送（挂账②）；多会话
- approval_pending 之外的挂起原因（collaborative_interrupt/budget_exhausted 已有）

## 4. 最小实现路径
- 最简单可行方案：api.py _run_task 路径的 QueryEngine 事件折叠层拦截 approval_requested 新事件 →
  落 turn_checkpoint（挂起态）→ 返回 RunSuspension → 新端点决策后经 run_resume 语义恢复
- 暂不引入：REPL 路径异步化、WebSocket、审批持久化（重启后挂起审批按 expired 处理并如实标注）
- 不做的抽象/配置化：挂起超时不做 config（沿用 300s 常量）

## 5. 技术决策
- 技术栈：stdlib + 既有 turn_checkpoint/journal/results 设施
- 证据协议：evidence: runs（机器任务 verify 命令由 scripts/spec_verify.py run 真实执行并记录 runs.jsonl；无命令任务写 `verify: manual: <理由>`；verify 命令不得自引用本包 check 门禁）
- 本轮允许改动：src/nexuscli/agent/results.py（SuspensionReason 已有）、agent/query_engine.py（折叠层）、
  runtime/api.py（_run_task 挂起分支 + 恢复端点）、runtime/approvals.py（挂起态登记）、
  agent/turn_checkpoint.py（挂起载荷）、tools/executor.py 最小触点（回调返回新决策类型时转事件）、tests/
- 本轮不应触碰：REPL 渲染、entrypoints/repl.py、llm/、docs/、.github/
- Git integration branch：`spec/2026-10-25_port-approval-async`

### 5.1 验证策略
- 范围级别：package
- 变更对象：Runtime 任务路径的审批挂起/恢复闭环
- 快速检查：`uv run python -m pytest -q tests/test_approval_async.py`
- 集成检查：`uv run python -m pytest -q tests/test_runtime.py tests/test_runtime_approvals.py tests/test_runtime_websession.py`
- 全项目检查：适用外——与 CI 一致的三步回归已由 task_0004 真实执行并全绿（1216 passed），升级条件见「升级触发」行
- 升级触发：若挂起分支破坏同步回调既有语义（F6 测试回归）即升级并记录

## 6. 成功标准与验证方式
- [x] 挂起：Runtime 任务遇审批 → 任务保持 running + RunSuspension(approval_pending) 事件/状态 +
  turn_checkpoint 落挂起态（pending 调用 + approval_id + revision） -> verify: task_0001
- [x] 决策恢复：Web decision(approve) → 按原 id 重派（journal 分流）→ 任务完成且文件落盘；
  decision(deny) → 合成 deny 结果任务完成 -> verify: task_0002
- [x] 超时 fail-closed：300s 无决策 → expired → 合成 deny（沿用 F6 口径） -> verify: task_0003
- [x] 同步通道回归：F6 同步阻塞回调与 REPL 路径零变化 -> verify: task_0004
- [x] 全量回归（CI 三步）不破 -> verify: task_0004
- [x] master A9 勾选、.spec/autorun/ 摘要写入 -> verify: task_0004

## 7. 风险与约束
- 风险点：挂起/恢复与 turn_checkpoint/journal 交互复杂 -> 05 分流表逐场景测试（execute/reuse/unknown）
- 风险点：_run_task 的 asyncio.run 单线程模型下挂起需释放执行线程 -> 挂起即 return（不阻塞），
  恢复走新执行（worker 模型天然支持）
- 风险点：RunSuspension 折叠与 done 事件的终端语义冲突 -> 折叠层先于 done 处理挂起事件
