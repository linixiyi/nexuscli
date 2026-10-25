# 对齐第 2 轮：审批与会话语义（A5-A8） - 项目范围

<!-- spec-format: 2 -->

## 1. 问题定义
- **项目目标**：按差距矩阵 high/medium 项对齐审批与会话语义——A5 审批卡片五态状态机
  （幂等门 + 参数截断禁 allow）、A6 会话权限模式与撤销（revision 乐观锁 + 切模式重判挂起 +
  断连清空挂起）、A7 EventBus correlation/event_seq 续号、A8 SessionStore 哈希提交链与孤儿修剪
- **目标用户**：工作台审批使用者（防未审先批/防双击）、依赖 SSE 游标的前端
- **核心价值**：审批面达到 kitagent 的状态机级严谨度；断线/崩溃可检测可恢复

## 2. 假设与待确认

### 2.1 已确认事实
- 契约来源：差距矩阵 high 的 A5/A6 行与 medium 的 A7/A8 行，含 kitagent 源路径
  （web/session.py:183-232、permissions/manager.py:66-108,389-404、core/events/bus.py:66-104、
  session/store.py:228,310）
- 现状：ApprovalBridge 内存登记表无状态机（任何 id 决策一次即删）；SSE 已有 per-thread
  自增 id 游标；SessionWriter 已有原子替换（第 1 轮加了 flush 纪律）
- AGENTS.md 固化语义不得破坏：allow 是长期信任不重复问；home 双重定向

### 2.2 关键假设
- A5/A6 同改 approvals.py → 同 lane 串行；A7/A8 文件面独立可并行
- 三 lane 并行（workflow），文件面不相交

### 2.3 待确认问题
- 无

### 2.4 可选解释与取舍
- 当前选择：A6 的权限模式存内存会话态（kitagent 同款，从不持久化为全局策略）-> 重启即重置

### 2.5 已证伪路径
- 无（暂无证伪记录）

## 3. 功能范围

### 3.1 核心功能（MVP）
- [x] A5 审批五态状态机 + 幂等门 + 参数截断禁 allow（照搬 web/session.py:183-232 语义）
- [x] A6 会话权限模式与撤销（revision 乐观锁 409 + 切模式重判挂起 + 断连清空）
- [x] A7 事件 correlation_id + run 级 event_seq 续号（照搬 core/events/bus.py:66-104 语义）
- [x] A8 transcript 哈希提交链 + 孤儿 tool_use 修剪（照搬 session/store.py:228,310）

### 3.2 扩展功能
- 无

### 3.3 不在范围内
- approval_pending 异步审批（A9，第 3 轮）；工作台权限控制前端件（A6 后端先行，前端挂第 4 轮）
- permission.toml 持久化 always 授权（kitagent storage，NexusCLI 有 permission_rules 等价）

## 4. 最小实现路径
- 最简单可行方案：approvals.py 状态机扩展 + websession 会话权限 + 事件发射点加 seq +
  store 写路径哈希链；三 lane 并行
- 暂不引入：前端权限控件（第 4 轮）、审批持久化
- 不做的抽象/配置化：会话模式枚举写死三态（kitagent 同款）

## 5. 技术决策
- 技术栈：stdlib（hashlib/uuid/threading）
- 证据协议：evidence: runs（机器任务 verify 命令由 scripts/spec_verify.py run 真实执行并记录 runs.jsonl；无命令任务写 `verify: manual: <理由>`；verify 命令不得自引用本包 check 门禁）
- 本轮允许改动：src/nexuscli/runtime/approvals.py、runtime/websession.py、runtime/api.py（A5/A6 端点）、
  agent/agent.py 与 runtime/api.py 的事件发射点（A7）、session/store.py 与 runtime/reads.py（A8）、tests/
- 本轮不应触碰：frontend/（第 4 轮）、tools/executor.py 门链、llm/、.github/、docs/ 正文
- Git integration branch：`spec/2026-10-25_port-approval-semantics`

### 5.1 验证策略
- 范围级别：package
- 变更对象：审批状态机、会话权限面、事件游标、transcript 完整性
- 快速检查：`uv run python -m pytest -q tests/test_approval_semantics.py tests/test_event_seq.py tests/test_transcript_integrity.py`
- 集成检查：`uv run python -m pytest -q tests/test_runtime_websession.py tests/test_runtime_approvals.py tests/test_runtime_sse.py`
- 全项目检查：适用外——与 CI 一致的三步回归已由 task_0004 真实执行并全绿（1202 passed），升级条件见「升级触发」行
- 升级触发：若事件形状变更破坏 REPL 渲染即升级并记录

## 6. 成功标准与验证方式
- [x] A5：五态流转 + 幂等决策（重复决策返回首次结果）+ input 超长截断时 allow 被拒 -> verify: task_0001
- [x] A6：模式配置 revision 乐观锁（过期 409）+ 切 read_only 重判挂起（原 allow 候选被拒）+ revoke_tool 单项撤销 + 断连清空挂起 -> verify: task_0002
- [x] A7：同 run 事件单调 event_seq；SSE 帧携带 seq 可作游标 -> verify: task_0003
- [x] A8：transcript 提交链哈希可检测尾部篡改；孤儿 tool_use 被修剪 -> verify: task_0004
- [x] 全量回归（CI 三步）不破 -> verify: task_0005
- [x] master A5-A8 勾选、.spec/autorun/ 摘要写入 -> verify: task_0006

## 7. 风险与约束
- 风险点：状态机改 ApprovalBridge 破坏 F6 既有语义 -> 既有 9 测试为回归锚；新增字段向后兼容
- 风险点：event_seq 改 SSE 帧形状 -> 帧新增字段向后兼容（前端不读则无影响），不改既有 id 游标
- 风险点：哈希链改写路径性能 -> 只追加不重算全链，写入开销 O(1)
