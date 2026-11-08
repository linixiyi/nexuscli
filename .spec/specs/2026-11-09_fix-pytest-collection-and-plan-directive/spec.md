# NexusCLI pytest 收集修复与 plan 指令收紧 - 项目范围

<!-- spec-format: 2 -->

## 1. 问题定义

- **项目目标**：① 新增空 `tests/__init__.py` 修复 `uv run pytest` 收集时 `ModuleNotFoundError: No module named 'tests'`（6 个测试文件使用 `from tests.…` 跨测试导入，裸 `pytest` 无法收集）；② 收紧 `src/nexuscli/agent/plan_execute.py` 的 `_PLAN_MODE_PLANNING_DIRECTIVE`：plan 模式白名单 read_file/glob/grep/search_code/vfs_ls/vfs_read，禁止 bash/execute_command（即使看似只读）与 pytest/npm/uv run/安装/构建/写盘任务安排，并同步 `docs/plan-execute.md`、`README.md` 与 `tests/test_plan_readonly.py` 断言。
- **目标用户**：在 zcode 中执行本包的 AI 代理，以及 NexusCLI 维护者。
- **核心价值**：pytest 两种调用方式行为一致；plan 模式的规划边界在提示词层面显式化，减少首批任务因 bash 被拦的浪费。
- **Literal ask**: 「新增 tests/__init__.py（空文件即可）… 修改 _PLAN_MODE_PLANNING_DIRECTIVE … 同步 docs/plan-execute.md、README.md … 在 tests/test_plan_readonly.py 增加 directive 断言」（2026-11-09 用户指令）。
- **Stated purpose**: 修收集、收指令、同步文档，且不回归已复测通过的既有修复。
- **Inferred outcome**: 三条件 pytest 验收全过；plan 指令含显式白名单与禁令并有断言锁定；confidence: high; evidence: 主会话已复现收集错误与 directive 位置（plan_execute.py:35/293）。
- **Constraints and exclusions**: 不改那 6 个测试文件的 import；不动已复测通过的 plan 假完成、workbench dist、headless status、cost null、prompt_breakdown 五项修复；不改 CI/billing 相关配置。
- **Trade-off preference**: 最小改动 + 文档如实；不为 directive 引入配置化。

## 2. 假设与待确认

### 2.1 已确认事实

- tests/ 无 `__init__.py`；`tests/test_approval_async.py`、`test_approval_semantics.py`、`test_event_seq.py`、`test_runtime_hardening.py`、`test_runtime_reads.py` 等 6 个文件使用 `from tests.test_runtime import …` 等跨测试导入。`uv run python -m pytest -q`（cwd 入 sys.path）可收集，裸 `uv run pytest -q` 在收集期报 ModuleNotFoundError——主会话 2026-11-09 复现。
- `_PLAN_MODE_PLANNING_DIRECTIVE` 位于 `src/nexuscli/agent/plan_execute.py:35`，plan 模式时注入 planner 提示（:293）。

### 2.2 关键假设

- A1 | 空 `tests/__init__.py` 足以让裸 pytest（prepend 模式）将 tests/ 识别为包并使 `from tests.…` 可导入，无需改测试文件 | system | `python -m pytest` 与裸 pytest 的差异正是 cwd 是否入 sys.path + 包识别，主会话已复现 | CONFIRMED | if false: escalate 报告实际收集错误，不擅自改 6 个文件的 import
- A2 | directive 只改提示字符串，不触碰 0034 的 plan 只读闸与假完成修复 | system | 只读闸在任务执行层而非 directive；boundary 限定仅改常量 | CONFIRMED | if false: 回滚 directive 改动并记录偏离
- A3 | tests 包化（模块名 test_foo → tests.test_foo）不引入 basename 冲突 | system | tests/ 全部平铺无同名文件，全量验收覆盖 | UNVERIFIED | if false: 记录冲突并 escalate

### 2.3 待确认问题

- [x] 是否改 6 个测试文件的 import？→ 不改（用户硬约束），只加空 `tests/__init__.py`。
- [x] 白名单是否含 vfs？→ 含 vfs_ls/vfs_read（用户给定清单）。
- [x] 真实 `--mode plan --yes` 行为验收由谁做？→ 主会话归档后真实冒烟（AGENTS.md：真实模型冒烟由主会话执行），确定性保证来自 0034 只读闸。

### 2.5 已证伪路径

- 无（暂无证伪记录；若执行中发现前提不成立，记录方案、证据与处理并回写本包）。

## 3. 功能范围

### 3.1 核心功能（MVP）

- tests 包化修复 pytest 收集（0001）。
- plan 指令白名单收紧 + 文档同步 + 断言（0002）。

### 3.3 不在范围内

- 不动五项已复测修复；不改 CI/billing 相关配置；不为 directive 引入配置化或新依赖。

## 4. 最小实现路径

- 最简单可行方案：0001 先行（一个空文件 + 全量三条件验收），0002 随后（常量 + 文档 + 断言），最终门禁跑用户指定的全套完成命令。
- 暂不引入：pytest 配置变更（importmode 等）、测试文件拆分、plan 工具白名单的运行时强制（只读闸已存在）。
- 不做的抽象：不为 directive 建模板系统。

### 4.1 方案阶梯

- B0 | literal ask | 空 `tests/__init__.py` + directive 常量收紧 + 文档/断言同步，两任务串行 | 用户指令的准确范围；机制前提已复现确认
- B1 | minimal change | 只做 0001 | pytest 收集修了但 plan 指令仍是宽口径，首批 bash 被拦的浪费仍在，不满足指令
- B2 | recommended alternative | 额外把白名单做成运行时强制闸（规划产物校验） | 更硬但越出指令范围且与 0034 只读闸重复

Recommendation: B0 because 两个前提均已复现确认且 boundary 清晰，最小改动即可满足三条件验收与行为验收。Flips if A1 is FALSIFIED.

## 5. 技术决策

- 证据协议：evidence: runs（每个机器 verify 必须由 spec_verify.py 真实执行并记录）
- 本轮允许改动：仅 tasks.md 中每个任务 boundary 列出的文件。
- 本轮不应触碰：五项已复测修复、CI/billing 相关配置、测试文件 import。
- Git integration branch：`spec/2026-11-09_fix-pytest-collection-and-plan-directive`

### 5.1 验证策略

- 范围级别：package
- 变更对象：tests 包标识、plan_execute.py 提示常量、两份文档与 test_plan_readonly 断言。
- 快速检查：`uv run pytest -q tests/test_plan_readonly.py`；`uv run pytest -q tests/test_runtime_reads.py`
- 集成检查：`uv run pytest -q`；`uv run python -m pytest -q`
- 全项目检查：适用外——最终门禁执行用户指定的完成命令（`uv run ruff check .`；`uv run ruff format --check .`；`uv run python scripts/check_mypy_ratchet.py`，本机 PyPI TLS 不通时以 UV_OFFLINE=1 走缓存；`uv run pytest -q`；`uv run python -m pytest -q`），仅在最终门禁出现非本包边界的失败时升级排查并记录理由。
- 升级触发：最终门禁出现非本包边界的失败时升级排查并记录理由；本包两任务边界清晰，不主动扩大范围。

<!-- spec:correction-log -->
### 5.5 修正日志

- none.

<!-- spec:deviation-log -->
### 5.6 偏离日志

- none.

## 6. 成功标准与验证方式

- [ ] `uv run pytest -q`、`uv run python -m pytest -q`、`uv run pytest -q tests/test_runtime_reads.py` 全部通过，未改 6 个测试文件的 import -> verify: task_0001
- [ ] plan 指令含六工具白名单并明示禁止 bash/execute_command 与 pytest/npm/uv run/安装/构建/写盘；docs/plan-execute.md 与 README 同步；test_plan_readonly 断言通过 -> verify: task_0002

## 7. 风险与约束

- 风险点：tests 包化改变测试模块名（test_foo → tests.test_foo），若存在 basename 冲突会报 import file mismatch。→ 缓解：0001 全量验收覆盖；tests/ 平铺无同名文件。
- 风险点：真实 `--mode plan --yes` 行为依赖 LLM 遵循指令。→ 缓解：0034 只读闸保证即使生成 bash 任务也 blocked、不得 COMPLETED、exit 非 0；主会话归档后真实冒烟并记录。
- 风险点：本机 PyPI TLS 当前不通，mypy 棘轮的 `uv run --with` 可能触发索引请求。→ 缓解：以 UV_OFFLINE=1 走缓存执行（已验证 mypy 2.4.1 可离线解析），并如实记录执行方式。
