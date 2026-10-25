# 对齐第 4 轮：实时推送/离线 Demo/e2e（A10-A12） - 项目范围

<!-- spec-format: 2 -->

## 1. 问题定义
- **项目目标**：A10 审批挂起/解决接入 SSE（消除前端 2s 轮询）+ token 合帧；A11 离线确定性
  Demo provider（无 key 端到端演示）+ e2e 启动器；A12 前端 seq-patch 应用器 + pending 幂等恢复
- **目标用户**：工作台用户（实时审批与流式体验）、贡献者（e2e 验收脚手架）
- **核心价值**：工作台实时性对齐 kitagent；无 key 也能端到端验收

## 2. 假设与待确认

### 2.1 已确认事实
- 契约来源：差距矩阵 high 的 A10/A11/A12 行（含 kitagent 源路径 web/session.py:13-266、
  web/demo.py:18-152、frontend/state.ts:5-28、useWorkbench.ts、e2e/workbench.spec.ts）
- A9 已落：审批挂起（approval_suspended 事件 + decision 恢复）；SSE 已有 since 游标 + 心跳
- A7 已落：events payload 含 per-thread seq

### 2.2 关键假设
- 三 lane 文件面不相交（A10: runtime/api.py + approvals.py；A11: llm/ 新文件 + scripts/；
  A12: frontend/src/ + frontend/e2e/）
- 离线 provider 不进默认 provider 选择（显式 mode 参数/环境开关触发）

### 2.3 待确认问题
- [x] 无（矩阵与 05 文档已定稿方向）

### 2.4 可选解释与取舍
- 当前选择：e2e 用 Playwright（kitagent 同款），仅 frontend job 可选步骤（构建门禁独立保留）

### 2.5 已证伪路径
- 无（暂无证伪记录）

## 3. 功能范围

### 3.1 核心功能（MVP）
- [x] A10 审批事件入 SSE + 50ms token 合帧 + 队列满 resync 帧
- [x] A11 离线确定性 Demo provider + scripts/e2e 启动器
- [x] A12 前端 seq-patch 应用器 + pending 幂等恢复 + Playwright e2e 基线

### 3.2 扩展功能
- 无

### 3.3 不在范围内
- WebSocket 通道；前端路由/状态库；CSP 收窄调优（第 1 轮已照搬）

## 4. 最小实现路径
- 最简单可行方案：A10 在 _send_event_stream 的事件查询里并入 approvals 内存表快照（挂起/解决
  即推送）+ token 合帧缓冲；A11 手写脚本化 provider（逐 8 字符流式）；A12 state.ts 纯函数应用器
- 暂不引入：WebSocket、UI 组件库、e2e 进 CI 门禁（先本地可跑）
- 不做的抽象/配置化：合帧窗口写常量 50ms（kitagent 同款）

## 5. 技术决策
- 技术栈：stdlib threading + React hooks + Playwright（devDependency）
- 证据协议：evidence: runs（机器任务 verify 命令由 scripts/spec_verify.py run 真实执行并记录 runs.jsonl；无命令任务写 `verify: manual: <理由>`；verify 命令不得自引用本包 check 门禁）
- 本轮允许改动：src/nexuscli/runtime/api.py、runtime/approvals.py、src/nexuscli/llm/（新增离线 provider 文件）、entrypoints/cli.py 或 bootstrap.py（离线 mode 注入缝）、scripts/web_validation_server.py（新）、frontend/src/、frontend/e2e/（新）、frontend/package.json（Playwright devDep）、tests/
- 本轮不应触碰：tools/、agent/agent.py、plan/、docs/ 正文、.github/workflows/ 门禁语义
- Git integration branch：`spec/2026-10-25_port-realtime-offline-e2e`

### 5.1 验证策略
- 范围级别：package
- 变更对象：SSE 事件面（新增审批事件）、离线 provider、前端状态层
- 快速检查：`uv run python -m pytest -q tests/test_runtime_sse.py tests/test_offline_demo.py`
- 集成检查：`cd frontend && npm run build` + `npx playwright test`（本地）
- 全项目检查：适用外——与 CI 一致的三步回归已由 task_0004 真实执行并全绿，升级条件见「升级触发」行
- 升级触发：若 SSE 帧格式不向后兼容（id/event 行变化）即升级并记录

## 6. 成功标准与验证方式
- [x] A10：审批挂起/解决经 SSE 推送（2s 轮询可删）；token 合帧不破坏帧序；队列满 resync 语义 -> verify: task_0001
- [x] A11：离线 provider 驱动真实 Agent 完成脚本化任务（含工具调用与审批门），无 API key -> verify: task_0002
- [x] A12：seq-patch 应用器断档触发重快照；pending 幂等恢复；e2e 基线用例本地可跑 -> verify: task_0003
- [x] 全量回归（CI 三步）不破 -> verify: task_0004
- [x] master A10-A12 勾选、.spec/autorun/ 摘要写入 -> verify: task_0005

## 7. 风险与约束
- 风险点：合帧改变帧时序破坏既有 SSE 测试 -> 既有 9 测试为回归锚；合帧仅合并同请求块的 token
- 风险点：Playwright 需要浏览器二进制 -> e2e 标记为本地可选，CI 不强制（记录）
- 风险点：离线 provider 泄漏进默认路径 -> 显式注入缝 + 测试锁默认行为
