# 轮次摘要：2026-10-25_add-tool-journal（master F9）

## 交付
- src/nexuscli/tools/journal.py：三态 SQLite 日志（journal_schema 版本化、CHECK 兜底、
  claim 四分支 execute/reuse/outcome_unknown/身份冲突、hash 级尽力去重查询、
  序列化结果规范化与坏数据类型化冲突）
- executor 分发点接线（审批门后 claim；write_file/edit_file 非 dry_run/bash 写类；
  只读直通不进日志）：reuse 回填不重执行、started 残留 fail-closed（error_type=outcome_unknown）、
  异常转 outcome_unknown 且 mark 失败原样上抛
- run_id 延续：ToolContext.journal/run_id 字段；Agent/PlanExecuteAgent 生成与透传
  （eval 直构 Agent 默认关闭，离线承诺不破）；ExecutionPlan.run_id 进 checkpoint；
  TaskRecord.run_id 列（alter-table 迁移，requeue 沿用）

## 证据
- runs.jsonl：task_0001-0005 机器验证；全量回归 1110 passed（新增 18 用例）；ruff/format 绿
- 关键修复记录：journal_claim 需在 try 内 validate 之前初始化（validate 抛错路径）；
  TaskManager scope 经 realpath 归一化，测试手插行必须用归一化值

## 简化决策（对照 spec §4 暂不引入清单）
- 无自动轮换（手动 SQL 写入文档，prune 挂账）；MCP/task 等其余副作用工具接入挂账；
  worker 级 journal 透传（plan worker/团队）挂 F10
