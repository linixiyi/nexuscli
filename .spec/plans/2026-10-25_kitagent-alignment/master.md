# kitagent 全面对齐（2026-10-25，来源：43 项差距矩阵 workflow）

> 来源：对齐调研 workflow 的差距矩阵（三 lane 实读盘点：kitagent 源码 /tmp/kitagent-src、
> NexusCLI 现状、43 项综合）。用户直接指令：可照搬的直接照搬（MIT，保留出处）。
> 矩阵判定：high 7 项、medium 6 项、其余 30 项为「已有等价/不对齐/不适用」（矩阵有逐条理由）。

## 目标

按矩阵 high/medium 项分轮对齐：先照搬零耦合小件（S），再做审批/会话语义对齐（M/L），
最后前端与 e2e 闭环。每项保留 kitagent 出处注释。

## 已确认事实

- kitagent 源码 /tmp/kitagent-src 实读可用；MIT 许可（LICENSE:1-2）
- NexusCLI 现状基线：8 轮改造后 1138 测试全绿、F14/F15 鉴权已按 kitagent 模型落地
- 矩阵明确的「不对齐/不适用」30 项不进本轮（逐条理由见矩阵 artifact：LangGraph/K8s/JSON-RPC
  守护进程/常驻 Core 等专属设施，或 NexusCLI 已有等价更优实现）

## 本轮特性（autorun 分轮实现）

### 第 1 轮：零耦合小件照搬（3 lane 并行）
- [ ] A1 HTTP 安全壳照搬：统一响应头 CSP/no-store/nosniff/no-referrer + 64KiB 体长上限(413) +
  非 GET JSON content-type 门(415)（kitagent web/app.py:106-128 直接照搬，出处注释）
- [ ] A2 写端点幂等门照搬：request_id+payload 摘要幂等登记（同 ID 异内容 409、容量满 429），
  接入 approvals/decision 等写端点（kitagent web/gateway.py:72-93）
- [ ] A3 文档鉴权滞后修复 + 机械断言：docs/workbench.md 与 runtime-api.md 补 bootstrap/csrf/cookie
  鉴权面；test_docs_integrity 增「runtime 文档覆盖当前鉴权面关键词」断言
- [ ] A4 事件日志 repair_tail 移植：崩溃半行截回 + 严格 seq 校验，并入 SessionWriter 写路径与
  reads.read_transcript（kitagent core/graph/event_log.py:29-50）

### 第 2 轮：审批与会话语义对齐
- [ ] A5 审批卡片五态状态机（pending/submitting/resolved/expired/unknown + 幂等门 +
  参数截断禁 allow）（kitagent web/session.py:183-232）
- [ ] A6 会话权限模式与撤销（read_only/ask/auto + revision 乐观锁 409 + revoke_tool +
  切模式即时重判挂起 + 断连清空挂起防泄漏）（kitagent permissions/manager.py:66-108,389-404）
- [ ] A7 EventBus correlation/event_seq 续号（kitagent core/events/bus.py:66-104）
- [ ] A8 SessionStore 哈希提交链 + 孤儿 tool_use 修剪（kitagent session/store.py:228,310）

### 第 3 轮：审批挂起语义（最大残留差距）
- [ ] A9 审批异步化：approval_pending 生产者接线（审批回调改异步决策 → turn_checkpoint 落挂起态
  → 决策回传恢复；input_hash 防参数漂移）（kitagent graph/interrupts.py:20-113 语义剥离 LangGraph）

### 第 4 轮：实时推送与前端闭环
- [ ] A10 审批事件入 SSE + token 合帧(50ms) + 队列满 resync（kitagent web/session.py:13-266）
- [ ] A11 离线确定性 Demo provider + e2e 启动器（kitagent web/demo.py + web_validation_server）
- [ ] A12 前端 seq-patch 应用器 + pending 幂等恢复 + Playwright e2e（kitagent frontend state.ts/useWorkbench/e2e）

### 第 5 轮：medium 收尾
- [ ] A13 运行报告冻结 + markdown 导出（kitagent projection.py:315-380）
- [ ] A14 协议文档生成链（kitagent scripts/generate_wire_protocol.py 模式）

## 边界

- 不引入 LangGraph/K8s/JSON-RPC 守护进程/常驻 Core（矩阵「不对齐/不适用」30 项有逐条理由）
- 照搬保留「Ported from kitagent (MIT, zed123214/kitagent)」出处注释
- 不破坏 AGENTS.md 固化语义（allow 语义/HOME 双重定向/进程组纪律等）

## 假设与开放问题

- 假设：/tmp/kitagent-src 在实现期间保持存在（若丢失从 GitHub 重拉）
- 开放：A9 异步审批与 F6 同步阻塞回调的兼容策略（A9 轮内定稿）
