# 工作台交互闭环（F6-F7） - 项目范围

<!-- spec-format: 2 -->

## 1. 问题定义
- **项目目标**：把 Runtime 任务的审批挂起呈现在 Web 上（F6 审批卡片：查询/批准/拒绝，与 REPL 同一审计口径），并支持从工作台发起 turn（F7：输入框 → POST thread/turn → SSE 实时观察）
- **目标用户**：localhost 上想直接在浏览器完成「发起-观察-批准」闭环的单人用户
- **核心价值**：Web 从纯观察面升级为交互面；审批不再只有 REPL 一个通道

## 2. 假设与待确认

### 2.1 已确认事实
- 设计契约：01-workbench-server.md（F6 审批桥：ApprovalBridge/callback_for/两端点/300s 超时失败关闭/Reason 行/审计 approver=hitl）与 02-workbench-frontend.md（F7 状态机、审批卡片组件、2s 轮询）
- F6 服务端语义锚点：_run_turn/_run_task 构造 QueryEngine 时接入 callback_for（api.py 当前不传 approval_callback）；执行器门链在回调之前（Web 无绕过面，01「业务逻辑·F6」）
- 第 2 轮已交付 SSE 流与前端四视图；App 外壳预留审批卡片区

### 2.2 关键假设
- 审批挂起期间任务保持 running（worker 心跳独立保活 lease），300s 无决策 deny 失败关闭
- F7 端到端验证需要环境配置 LLM key；无 key 环境以「POST 400 + 事件流零新帧」的错误路径断言替代并如实标注（02 验收口径）

### 2.3 待确认问题
- 无

### 2.4 可选解释与取舍
- 当前选择：Web 端只提供 approve/deny 两动词 -> approve_run/approve_auto 是 REPL 会话语义，避免 Web 一键放大信任（01 定稿）
- 当前选择：审批通知用 2s 轮询而非 SSE 推送 -> 挂起/解决的 SSE 推送为 01 挂账②，MVP 用轮询

### 2.5 已证伪路径
- 无（暂无证伪记录）

## 3. 功能范围

### 3.1 核心功能（MVP）
- [ ] F6 Web 审批卡片：服务端 ApprovalBridge + GET /v1/approvals + POST /v1/approvals/{id}/decision；前端卡片渲染（description 作 Reason 行）与 2s 轮询
- [ ] F7 Web 端发起 turn：Composer 输入框 → POST threads/turns → SSE 实时观察；线程记忆写入 localStorage

### 3.2 扩展功能
- 无

### 3.3 不在范围内
- Web 端 approve_run / approve_auto；审计词表新增 approval-timeout 值（executor.py 改动，挂账）
- dist 打进 wheel；前端单测框架；审批 SSE 推送（挂账②）

## 4. 最小实现路径
- 最简单可行方案：runtime/approvals.py 单文件桥（内存登记表 + threading.Event），_run_turn/_run_task 各一处接线；前端 ApprovalCard 组件 + Composer 状态机
- 暂不引入：审批持久化（进程生命周期即审批生命周期）、多用户会话、决策令牌
- 不做的抽象/配置化：APPROVAL_TIMEOUT_SECONDS=300 保持模块常量（01 挂账④）

## 5. 技术决策
- 技术栈：Python stdlib（threading.Event）+ React 既有栈
- 证据协议：evidence: runs（机器任务 verify 命令由 scripts/spec_verify.py run 真实执行并记录 runs.jsonl；无命令任务写 `verify: manual: <理由>`；verify 命令不得自引用本包 check 门禁）
- 本轮允许改动：src/nexuscli/runtime/（新 approvals.py + api.py 接线与路由）、frontend/src/（ApprovalCard、Composer、App 接线）、tests/test_runtime_approvals.py、frontend/dist（重建）
- 本轮不应触碰：src/nexuscli/tools/executor.py（审批门序不动）、src/nexuscli/agent/、docs/、.github/
- Git integration branch：`spec/2026-10-25_add-workbench-interaction`

### 5.1 验证策略
- 范围级别：package
- 变更对象：Runtime API 审批面（新 approvals 模块与两端点）、_run_turn/_run_task 接线、前端 Composer/卡片
- 快速检查：`uv run python -m pytest -q tests/test_runtime_approvals.py`
- 集成检查：`cd frontend && npm ci && npm run build` + dist in sync
- 全项目检查：适用外——审批门序零改动，无跨模块契约变化；与 CI 一致的三步回归由收口任务执行
- 升级触发：若实现需触碰 tools/executor.py（门序/审计词表）即升级为 integration 并记录理由

## 6. 成功标准与验证方式
- [ ] ApprovalBridge 单元语义：登记/查询/决策/二次决策 404/非法动词 400/超时 deny(resolved_by=timeout) -> verify: task_0001
- [ ] 决策端点 Origin 语义与 MCP A3 口径一致（非 loopback Origin 403、无 Origin 放行） -> verify: task_0002
- [ ] 执行器集成：write_file 挂起 → approve 落盘 + 审计 approver=hitl；deny 拒绝；deny 规则下桥登记表恒空 -> verify: task_0003
- [ ] Reason 契约：两个调用点的审批请求 description 键存在且非空 -> verify: task_0004
- [ ] _run_turn/_run_task 接入审批回调（既有 runtime 测试不回归） -> verify: task_0005
- [ ] 前端 ApprovalCard + 2s 轮询 + Composer 状态机交付，构建通过 -> verify: task_0006
- [ ] 全量回归（CI 三步）+ 端到端 smoke -> verify: task_0007
- [ ] master F6/F7 勾选、.spec/autorun/ 摘要写入 -> verify: task_0008

## 7. 风险与约束
- 风险点：审批挂起占死 worker 槽位 -> 缓解：300s 超时 deny 失败关闭（默认 2 worker）
- 风险点：Web 决策绕过写域守卫/allow 语义 -> 缓解：门序在回调之前（01 已核实 executor.py:136-151），Web 决策只回答已到达回调的问题；测试锁 deny 规则下桥登记表恒空
- 风险点：审计词表偏差（超时 deny 记 approver=hitl） -> 如实披露（01「数据流·审批数据流」第 5 步），词表改动挂账
