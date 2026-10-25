# 工具副作用日志与幂等重放（F9） - 任务拆解

## 使用规则
- 每个任务都要写清楚 `boundary` 和 `verify`；机器任务的 verify 以首个反引号 span 为执行的命令
- 发现的可执行问题必须回写本包，并在本轮做完
- verify 命令不得自引用本包 check 门禁

## 阶段一：日志模块

- [x] src/nexuscli/tools/journal.py：三态日志与 claim 协议
  - boundary: 只新建 journal.py（journal.db 初始化、journal_schema 元数据表 version=1、tool_invocations DDL 带 CHECK 约束、claim_tool 四分支、complete_tool、mark_outcome_unknown、ToolJournalConflictError/ToolJournalSchemaError 类型化异常、input_hash 规范化序列化）与 tests/test_tool_journal.py；不改执行器
  - verify: `uv run python -m pytest -q tests/test_tool_journal.py`

## 阶段二：接线

- [x] run_id 延续：plan checkpoint 与 TaskRecord
  - depends-on: task_0001
  - boundary: 只改 plan/models.py（ExecutionPlan 增 run_id 可选字段并进 to_dict/from_dict，缺省视为新 run）与 runtime/tasks.py（TaskRecord 增 run_id 列，既有 alter-table 迁移惯例）；agent 侧 run_id 生成的最小注入接缝；新增对应断言进既有测试文件或新 tests/test_executor_journal.py
  - verify: `uv run python -m pytest -q tests/test_plan_checkpoint.py tests/test_executor_journal.py`
- [x] executor 分发点接入 + hash 级去重
  - depends-on: task_0002
  - boundary: 只改 tools/executor.py 的单一副作用分发点（write_file/edit_file 非 dry_run/bash 写类三类，readonly 直通不进日志；claim→execute/reuse/outcome_unknown/conflict 映射；complete/异常转 outcome_unknown；写失败上抛）与 journal.py 的 hash 级二级查询；tests/test_executor_journal.py 扩展四场景
  - verify: `uv run python -m pytest -q tests/test_executor_journal.py tests/test_tool_journal.py tests/test_bash_readonly.py tests/test_tool_approval_matrix.py`

## 阶段三：收口

- [x] 全量回归（CI 三步）
  - depends-on: task_0003
  - boundary: 不改产品文件；跑 CI 三步；失败修复限于本包 boundary
  - verify: `uv run ruff check . && uv run ruff format --check . && uv run python -m pytest`
- [x] 计划同步：勾选 master F9 + .spec/autorun/ 摘要
  - depends-on: task_0004
  - boundary: 只改 master（F9 置 [x]）与 .spec/autorun/ 摘要
  - verify: `grep -c "\[x\] F9 " .spec/plans/2026-10-25_workbench-and-readme/master.md`（期望输出 1）
