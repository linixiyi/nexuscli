# 轮次摘要：2026-10-25_port-approval-semantics（对齐 A5-A8）

## 交付（/workflow 三 lane 并行移植，出处注释保留；conclusion 的「1 条失败」为
# openIssues 含「不可失败」字样的字符串误报，三 lane 实际全部成功——主会话核实）
- A5（approvals.py）：审批五态 pending/submitting/resolved/expired/unknown + decide 幂等
  （重复决策重放首次结果）+ input>4KiB 截断禁 allow（防未审先批）+ 修复超时覆写并发决策竞态
- A6（websession.py/api.py）：SessionPermissions{mode,revoked_tools,revision} 内存态 +
  PATCH /v1/workbench/permissions（revision 乐观锁 409）+ 权限变更即时重判挂起（双向：
  read_only/revoked deny 收口、auto 放行等待调用，kitagent manager.py:99-108 同款）+
  callback_for 接会话模式门（read_only/auto/revoked 短路不登记卡片；allow 语义不破坏）
- A7（api.py）：events 表 payload 增 per-thread 单调 seq（自 1 起 +1，重启经 count(*)+1 续号）；
  SSE data 透出 seq（帧格式向后兼容）；事件类型集不变
- A8（store.py/reads.py）：transcript 行级哈希提交链（_chain 字段，O(1) 追加，_rewrite 重挂）+
  read_transcript verify_chain 开关（断链如实标注不抛异常）+ 孤儿 tool_use 计数标注

## 证据
- runs.jsonl：task_0001-0005 机器验证；全量回归 1202 passed（三 lane 62 个新用例）；ruff/format 绿

## 挂账（如实）
- 会话 auto 模式放行的调用审计记 approver="hitl"（executor 合同无 session_auto 通道，后续加）
- PATCH/GET permissions 端点为小步扩面（GET 为构造 expected_revision 的前提，已报备）
- 旧 events 行无 seq 键（外部消费者直读 payload["seq"] 会 KeyError）；thread_id 微秒撞号
  （既有隐患，建议后续改 uuid）；哈希链为字段级方案（整行剥除 _chain 本行不可检测，已入档）
