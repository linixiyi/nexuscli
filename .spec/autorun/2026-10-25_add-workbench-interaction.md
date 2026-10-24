# 轮次摘要：2026-10-25_add-workbench-interaction（master F6/F7）

## 交付
- F6：runtime/approvals.py（内存登记表 + callback_for + 300s 超时失败关闭）+ 两端点
  （GET /v1/approvals、POST decision，仅 approve/deny，Origin 口径同 MCP A3）+
  _run_turn/_run_task 接线；门链在回调之前（Web 无绕过面，测试锁 deny 规则下登记表恒空）
- F7：Composer 提交状态机（建线程 → 记忆 → 后台 POST turn → SSE 观察 → 终态兜底）+
  ApprovalCard（Reason 行）+ App 常驻 2s 轮询

## 证据
- runs.jsonl：task_0001-0006 机器验证；全量回归 1101 passed（新增 9 用例）；ruff/format 绿
- 真实进程 smoke：approvals 端点空表正常；F7 端到端在配置了 LLM key 的环境**直接跑通**
  （POST turn 返回 200 与真实模型文本，超出 02 的错误路径降级口径）

## 简化决策
- 审批通知用 2s 轮询（SSE 推送挂账②）；超时 deny 审计词表偏差如实披露不修（挂账）
