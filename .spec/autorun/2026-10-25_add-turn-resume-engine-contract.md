# 轮次摘要：2026-10-25_add-turn-resume-engine-contract（master F10/F11）

## 交付
- F11：agent/results.py（RunOutcome | RunSuspension sum type、三态挂起——approval_pending
  挂账不接线、错误码表 + classify_error + fold_engine_result）；发射点标注（流错误
  llm_error、plan/team 异常 classify、预算耗尽 warning 加 kind 标记）；PlanCheckpointMissing
  类型化升级；QueryEngine ask/plan/team_result_async 三变体（既有 raise 语义保留）
- F10：agent/turn_checkpoint.py（~/.nexuscli/turn_checkpoints/、temp+rename 原子写、
  cwd 过滤列表、损坏/非法 id 降级）；_run_react 双快照点（in-flight 含 pending 原始 id /
  轮末覆盖为空）；Agent.continue_run()（不追加用户消息）；REPL /turn-resume（列出/接续、
  pending 按原 id 经 executor+journal 重派 execute/reuse/outcome_unknown）

## 证据
- runs.jsonl：task_0001-0006 机器验证；全量回归 1122 passed（新增 12 用例）；ruff/format 绿
- 三套持久化真相分工表已按 05 定稿落进 turn_checkpoint.py 模块文档

## 简化决策（对照 spec §4 暂不引入清单）
- 无 checkpoint 加密/TTL（沿用 plan_runs 明文口径）；顶层 --resume-turn flag 挂账；
  approval_pending 生产者挂账（同步阻塞审批无挂出出口）
- 边界偏差（如实记录）：/turn-resume 属用户可见命令，docs/repl-commands.md 补一行说明
  （本包 boundary 原写「不应触碰 docs/」，按「发现的问题回写本轮」原则处理并在摘要声明）
