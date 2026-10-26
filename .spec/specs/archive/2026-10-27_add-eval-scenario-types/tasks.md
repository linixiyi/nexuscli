# eval 新场景类型：多轮用户模拟与记忆召回评测 - 任务拆解

## 使用规则
- 每个任务都要写清楚 `boundary` 和 `verify`；机器任务的 verify 以首个反引号 span 为执行的命令
- 如果一个任务没有验证方式，就不能开始；确无命令可执行的任务写 `verify: manual: <理由>`
- 发现的可执行问题必须回写本包，并在本轮做完
- 新任务包使用 `YYYY-MM-DD_<verb>-<object>`，详见 `references/naming-and-commits.md`
- verify 命令不得自引用本包 check 门禁（自指命令在「最近一条」语义下不可满足）

## 阶段一：实现
- [x] 多轮用户模拟场景类型（schema + runner 多轮驱动 + 跨轮断言 + ≥2 内置示例 + 测试）
  - boundary: 只改 src/nexuscli/eval/（loader.py 的 schema 与校验、runner.py 的多轮
    驱动、reporter.py 如需透出轮数）、examples/evals/ 新增示例、tests 中 eval 相关；
    不动 src/nexuscli/agent/ 与 src/nexuscli/memory/
  - verify: `uv run --extra dev python -m pytest -q -k eval`
- [x] 记忆召回评测场景类型（seed + recall 排序断言 + ≥2 内置示例 + 测试）
  - depends-on: task_0001
  - boundary: 只改 src/nexuscli/eval/（新增记忆评测执行路径与断言）、examples/evals/
    新增示例、tests 中 eval 相关；MemoryManager 只读使用，临时 DB 走 pytest tmp 路径
  - verify: `uv run --extra dev python -m pytest -q -k eval`
- [x] 文档同步与全量回归
  - depends-on: task_0002
  - boundary: 只改 docs/eval.md（schema 说明、断言表、Scope 一节）与 README 特性表
    「评测回归」行；不改 src 与 tests
  - verify: `uv run --extra dev python -m pytest -q && uv run --extra dev ruff check . && uv run --extra dev ruff format --check .`
- [x] 真实入口验收
  - depends-on: task_0003
  - boundary: 只在仓库根运行真实入口 `nexuscli eval run`，不产生任何代码改动
  - verify: `uv run nexuscli eval run`

---

**当前进度**：由脚本计算，无需手动维护
