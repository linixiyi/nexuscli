# nexusCLI 参考项目差距四项补齐任务包 - 任务拆解

## 使用规则

- 每个任务带 `boundary` 与 `verify`；写集互不相交（四工位并行前提）。
- README.md 与既有 docs/*.md 的更新统一归集成阶段，实现任务只写代码、测试与新建文件。

## 阶段一：四工位并行实现（写集两两不相交）

- [x] G1 Agent 评测回归框架 nexuscli eval
  - id: task-eval-regression
  - boundary: src/nexuscli/eval/**（新建）、src/nexuscli/entrypoints/cli.py（仅加 eval 子命令）、examples/evals/**（新建）、docs/eval.md（新建）、tests/test_eval.py（新建）
  - verify: uv run python -m pytest tests/test_eval.py 通过（坏 schema 报错、内置场景跑通、回归对比逻辑）；nexuscli eval list/run 可用；真实 LLM 不被调用
- [x] G2 工具循环调用检测
  - id: task-loop-detection
  - boundary: src/nexuscli/agent/loop_guard.py（新建）、src/nexuscli/agent/agent.py、src/nexuscli/config.py（仅 agent 段）、tests/test_loop_detection.py（新建）、tests/test_agent_turns.py:195（经升级提问获准的单一断言更新）
  - verify: tests/test_loop_detection.py 覆盖阈值触发/未触发/关闭/换参重置/两轮终止；pytest 相关文件全绿
- [x] G3 run 级授权（审批第 4 档）
  - id: task-run-scope-approval
  - boundary: src/nexuscli/entrypoints/commands/approval.py、src/nexuscli/tools/executor.py、src/nexuscli/tools/ 下 ToolExecutionContext 定义文件、tests/test_run_scope_approval.py（新建）
  - verify: tests/test_run_scope_approval.py 覆盖 r 免提示/异工具仍提示/deny 仍拦/审计 approver=run-scope/新 run 清空；测试 patch HOME 不污染真实审计
- [x] G4+G5 跨会话历史搜索 + usage 续算
  - id: task-history-and-usage
  - boundary: src/nexuscli/session/**、src/nexuscli/entrypoints/commands/session_cmds.py、src/nexuscli/entrypoints/commands/usage.py（仅展示口径）、src/nexuscli/entrypoints/repl.py（仅 /history 分发与 usage 基线接线）、tests/test_history_search.py 与 tests/test_session_usage.py（新建）、tests/test_session_store.py
  - verify: 匹配/--all/空参数、usage 行写入、未知 record 类型容错、恢复累计、/clear//resume//fork 四路径 process_usage 重置；CLI 单次 --json session_usage 双口径

## 阶段二：独立复审与集成（主线验收）

- [x] 独立复审合并改动（跨工位口径冲突、行为矛盾、文档漂移、测试互染、要求遗漏）
  - id: task-independent-review
  - boundary: 只读全工作树
  - verify: 复审员附证据的 findings 全部交集成处置（8 条，含 process_usage 跨切换虚高、--json 缺双口径两条 medium）
- [x] 集成修复与文档同步
  - id: task-integration-docs
  - boundary: 复审 findings 涉及文件、README.md、docs/permissions.md、docs/observability.md、docs/subagents.md
  - verify: ruff check / ruff format --check 通过；受影响测试文件全绿；README 交互命令表含 /history 与 nexuscli eval、run 级审批、loop_detection_threshold、usage 续算口径均已如实更新
- [x] 全量校验门与主线验收
  - id: task-final-gate
  - boundary: 无文件改动（验证任务）
  - verify: uv run ruff check . 通过；uv run ruff format --check . 通过（171 files）；uv run python -m pytest → 590 passed；真实 LLM 冒烟 3/3（cli-react / cli-plan / sdk-react，模型 cbcn/glm-5.3-flash，独立复核确认）
