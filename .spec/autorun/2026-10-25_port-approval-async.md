# 轮次摘要：2026-10-25_port-approval-async（对齐 A9，最大残留差距收口）

## 交付
- 审批挂起即 checkpoint：ApprovalSuspension(BaseException) 穿透 executor except-Exception
  直达 _run_task 捕获点；挂起模式回调（suspend=True 仅 _run_task 触发，REPL/plan/team 保持
  F6 阻塞桥零变化）；checkpoint (a) 落盘即恢复依据；任务 running + 心跳保活
- 决策恢复：decision 端点零阻塞（event.set() 即恢复信号）→ worker 原地重建 checkpoint →
  pending 按原 id 重派（journal 分流）→ approve 后文件落盘、deny 合成拒绝、300s expired
  fail-closed；持久决策查找（同 scope+tool+input_hash 已收口决策直接复用，kitagent
  durable 同位语义）——同任务内同参重调用免重复问
- EngineResult：fold_engine_result 接 approval_suspended → RunSuspension(approval_pending)，
  F11 挂账的生产者自此接通（docstring 已更新）

## 证据
- runs.jsonl：task_0001-0005 机器验证；全量回归 1216 passed；ruff/format 绿；
  mypy 对 4 个改动源文件零错（不在存量排除清单，CI 纪律满足）

## 有意作用域边界（如实）
- react 任务路径专属：plan/team 任务遇审批仍 F6 阻塞桥（挂起恢复依赖回合 checkpoint 与
  按 id 重派，为 react 特有）；并行只读 batch 兄弟调用的 journal 行可能停在 started
  （恢复按 fail-closed 报 outcome_unknown，未优化）；worker 等待用同步 event.wait
  （彼时循环空闲，未来加并发协程需 run_in_executor）
