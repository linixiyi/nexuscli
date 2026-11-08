# NexusCLI pytest 收集修复与 plan 指令收紧 - 任务拆解

## 使用规则

- 每个任务只改 `boundary` 列出的文件；发现相邻问题先记录，不顺手修。
- 机器任务的 `verify` 以首个反引号 span 为执行命令；命令必须在仓库根目录可执行。
- 0001 先行：0002 依赖其全量验收在稳定树上通过。
- 用户硬约束：不改那 6 个测试文件的 import；不动已复测通过的 plan 假完成、workbench dist、headless status、cost null、prompt_breakdown 五项修复。

## 阶段 A：修复与收紧（机器任务）

- [x] 0001 修复 tests 包导入收集（新增空 tests/__init__.py）
  - boundary: 新增 `tests/__init__.py`（空文件即可）；不得改任何既有测试文件的 import。
  - 行为：修复 `uv run pytest -q` 收集时 `ModuleNotFoundError: No module named 'tests'`。tests/ 下 6 个文件（test_approval_async.py、test_approval_semantics.py、test_event_seq.py、test_runtime_hardening.py、test_runtime_reads.py 等）使用 `from tests.…` 跨测试导入；空 `__init__.py` 使 prepend 导入模式将 tests/ 识别为包，裸 `pytest` 亦可收集。
  - 测试：三条件验收全过，且全量无回归（含既有 141+ 测试文件）。
  - verify: `uv run pytest -q && uv run python -m pytest -q && uv run pytest -q tests/test_runtime_reads.py`
  - evidence: uv run pytest -q && uv run python -m pytest -q && uv run pytest -q tests/test_runtime_reads.py -> exit 0, 修复前基线复现 ModuleNotFoundError: No module named 'tests'（tests/test_runtime_reads.py:18）；新增空 tests/__init__.py 后 verify 链 exit 0，两遍全量均 100% 无失败（少量 s 跳过），tests/test_runtime_reads.py 单独复跑 5 passed in 26.71s

- [x] 0002 plan 指令白名单收紧与文档同步
  - depends-on: task_0001
  - boundary: `src/nexuscli/agent/plan_execute.py`（仅 `_PLAN_MODE_PLANNING_DIRECTIVE` 常量）、`docs/plan-execute.md`、`README.md`（仅 --mode plan 说明段）、`tests/test_plan_readonly.py`。
  - 行为：directive 明确 plan 模式只允许 read_file/glob/grep/search_code/vfs_ls/vfs_read；禁止 bash/execute_command（即使命令看似只读）；禁止安排 pytest/npm/uv run/安装/构建/写盘任务。docs/plan-execute.md 与 README 的 --mode plan 说明同步该语义。tests/test_plan_readonly.py 增加断言：directive 含六个白名单工具名、明示禁止 bash/execute_command、明示禁止 pytest/npm/uv run/安装/构建/写盘字样；既有用例不回归。
  - 测试：test_plan_readonly.py + headless_status/plan 回归。真实 `--mode plan --yes` 行为验收（不再首批因 bash 被 blocked；若仍生成 bash 任务保持 blocked、不得 COMPLETED、exit 非 0）由主会话在归档后真实冒烟验证并记录——确定性保证来自 0034 只读闸（blocked 不得标 COMPLETED、exit 非 0）。
  - verify: `uv run pytest -q tests/test_plan_readonly.py tests/test_headless_status.py tests/test_plan.py`
  - evidence: uv run pytest -q tests/test_plan_readonly.py tests/test_headless_status.py tests/test_plan.py -> exit 0, 50 passed in 0.40s（基线 49 + 新增指令白名单断言 1，exit 0）；ruff format/check 全过

---

**当前进度**：由脚本计算，无需手动维护
