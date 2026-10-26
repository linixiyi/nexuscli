# Workbench UX 第二轮迭代 - 项目范围

<!-- spec-format: 2 -->

## 1. 问题定义
- **项目目标**：在五包合并后的完整 main 上做第二轮实测（demo 旅程 + 真实 LLM 路径
  抽查），修掉真路径暴露的三个体验缺口。
- **目标用户**：真实 LLM 环境下的 workbench 用户（非 demo）。
- **核心价值**：长时间等待不再无反馈、会话列表可辨识、报告证据可核对。

## 2. 假设与待确认

### 2.1 已确认事实
- 真路径抽查（/tmp/ux2-real/02-real-streaming.png，真实 glm-5.3-flash 网关）：
  turn 提交后停在 seq 2（仅 turn.started），「执行中」无任何后续反馈超过 3 分钟，
  无取消手段——当日网关本就不稳（同日 provider 1210 / SSL 断连），慢与挂是
  真实会撞的场景
- 会话回放列表出现 "(untitled) 0 条消息" 与同名重复条目（hello ×2），无法区分
- 上轮挂账 U6：报告结果 tab 的「执行事实与来源」只有一行聚合按钮；
  derive.ts 的工具清单数据现成
- demo 旅程在合并 main 上 18 张全过、零页错误（/tmp/ux2-baseline），UI 面与
  ux-iteration after/ 一致

### 2.2 关键假设
- 「30s 无新事件 → 渐进提示」可以用前端现有 SSE 事件计数实现（events.length
  停滞时长），不需要心跳协议改动
- 会话列表可辨识只需前端展示层改动（localThreads 标题已有；REPL 会话加时间戳/
  首条消息摘要有 SessionMeta.title/message_count 可用，或请求 transcript 首条——
  最小面用 title 为空时回退显示 created_at 日期）

### 2.3 待确认问题
- [x] turn 取消端点本轮做不做？→ 结论：不做（后端取消语义需单独设计：同步 POST
  在途、worker lease、审批挂起交互），本轮只做前端可见性（慢等待提示），
  取消挂账为独立后端包
- [x] U8 DEMO 横幅？→ 结论：继续挂账（需要协议面暴露 provider 信息）

### 2.4 可选解释与取舍
- 当前选择：慢等待提示阈值 30s（events 计数 + updated 时间戳对比），文案
  「仍在等待模型响应…（网络慢或模型负载高时可另开会话）」-> 纯前端、可测
- 当前选择：REPL 会话条目回退显示 = title 为空用「会话 YYYY-MM-DD HH:MM」+ 条数，
  不额外拉 transcript -> 零请求增量

### 2.5 已证伪路径
- 无（暂无证伪记录；条目写：方案概述 + 证伪依据出处 + 日期）

## 3. 功能范围

### 3.1 核心功能（MVP）
- [x] W1 慢等待反馈：执行中且 30s 无新事件时 run-inline 追加渐进提示（不改变
  busy 语义、不自动取消）
- [x] W2 会话回放可辨识：title 为空回退「会话 <日期时间>」，条目小字含条数与
  日期；同名条目按 updated_at 排序去视觉混淆
- [x] U6 报告证据列表：结果 tab 的「执行事实与来源」逐工具列出（名称+状态+点击
  跳执行详情），替换单行聚合按钮
- [x] 回归：demo journey 全绿 + 新增断言；真路径抽查重跑（有网时）

### 3.2 扩展功能
- 无

### 3.3 不在范围内
- turn 取消端点（后端包挂账）；U8 DEMO 横幅（协议面挂账）；多信号召回 UI 化

## 4. 最小实现路径
- 最简单可行方案：ChatPanel 一个 useEffect 计时器（busy 且 events.length 停滞
  30s → 提示态）；Sidebar 条目回退格式化；Inspector 结果 tab 用 deriveTools
  渲染逐条证据列表（复用 tool-row 视觉）
- 暂不引入：心跳协议、取消端点、虚拟滚动
- 不做的抽象/配置化：阈值不做 config；列表不做搜索/分组

## 5. 技术决策
- 技术栈：不变
- 证据协议：evidence: runs（机器任务 verify 命令由 scripts/spec_verify.py run 真实
  执行并记录 runs.jsonl；无命令任务写 verify: manual: <理由>）
- 本轮允许改动：frontend/src/{views/ChatPanel.tsx,views/Sidebar.tsx,views/
  Inspector.tsx}、frontend/e2e/workbench.spec.ts（新断言）、docs/workbench.md（功能
  面一句）
- 本轮不应触碰：useWorkbench/SSE 协议、后端任何代码、既有断言词汇
- Git integration branch：`spec/2026-10-27_workbench-ux-round2`

### 5.1 验证策略
- 范围级别：package
- 变更对象：三个视图组件 + e2e + 文档一句
- 快速检查：`cd frontend && npm run build && npx playwright test e2e/workbench.spec.ts`
- 集成检查：`uv run python scripts/ui_journey.py --out /tmp/ux2-after`
- 全项目检查：适用外：纯前端视图层增量；由发布门禁统一执行
- 升级触发：只有改动共享基础设施、跨模块契约、构建/依赖、发布载荷，或
  package/integration 检查暴露跨边界回归时，才把范围升级并记录理由

## 6. 成功标准与验证方式
- [x] W1 慢等待提示落地且有 e2e 断言 -> verify: task_0001
- [x] W2 会话条目可辨识（回退标题+日期）-> verify: task_0001
- [x] U6 报告证据逐条列出且可跳转 -> verify: task_0001
- [x] journey 全绿 + 构建通过 -> verify: task_0002

## 7. 风险与约束
- 风险点：计时器提示在 demo 快链路误触发 -> 缓解：阈值 30s 远大于 demo 链路耗时，
  e2e 用假计时注入（violate timers 不引，改用小阈值参数化内部常量不可行则用
  事件计数停滞模拟）

### 5.4 编排策略
- route: local
- immediate blocker: 三项修均为单文件视图层小改，主线程经 /workflow 单实现者完成
- ownership: 实现 1 lane；check 轮主会话自查 + journey 对照
- waiting strategy: task_0002 等 task_0001
- verification gate: build + e2e（含新断言）+ journey 零页错误 + docs/ruff 绿
