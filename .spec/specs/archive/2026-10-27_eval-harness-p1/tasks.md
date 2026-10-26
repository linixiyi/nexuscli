# 离线 eval harness 三项增强 - 任务拆解

## 使用规则
- 每个任务都要写清楚 `boundary` 和 `verify`；机器任务的 verify 以首个反引号 span 为执行的命令
- 如果一个任务没有验证方式，就不能开始；确无命令可执行的任务写 `verify: manual: <理由>`
- 发现的可执行问题必须回写本包，并在本轮做完
- verify 命令不得自引用本包 check 门禁（自指命令在「最近一条」语义下不可满足）
- 编排：任务经动态工作流并行/串行实现（任务一 ∥ 任务三 → 任务二 → 文档 → 独立评审
  → 有界修复 → 全量门），主会话只做验收收口

## 阶段一：实现

- [x] task_0001 轨迹断言增强（required_tools / max_tool_calls / max_total_tool_calls）
  - boundary: 只改 src/nexuscli/eval/loader.py（EvalAssertions、_parse_assertions）与
    src/nexuscli/eval/runner.py（_assertion_failures）、新建 tests/test_eval_assertions_p1.py、
    examples/evals/ 新增 ≥1 个内置场景（编号顺延）；不改既有四种断言语义与 runner 主循环
  - verify: `uv run python -m pytest tests -q -k eval_assertions`（前置：仓库根运行；
    成功判定：exit 0 且三种断言的正/反例、schema 报错（指名文件）均有测试覆盖；
    失败处理：先看新断言的解析分支，再查 runner 判定结构）
- [x] task_0002 场景难度分档（difficulty 字段 + 按档聚合）
  - depends-on: task_0001, task_0003
  - boundary: 只改 src/nexuscli/eval/loader.py（scenario schema 加可选 difficulty，
    严格校验 short/medium/long）与 src/nexuscli/eval/reporter.py（build_report 按档
    聚合、format_report summary 分档视图）、新建 tests/test_eval_difficulty_p1.py
  - verify: `uv run python -m pytest tests -q -k eval_difficulty`（成功判定：exit 0 且
    三档合法/非法/缺省、聚合正确性、「退化集中在哪一档」视图均有测试；失败处理：
    先查 schema 校验分支，再查聚合键）
- [x] task_0003 成本确定性指标（turns / total_tool_calls 进报告 + diff 告警）
  - boundary: 只改 src/nexuscli/eval/reporter.py（build_report 写入 turns 与
    total_tool_calls=len(tool_sequence)、与上次报告 diff、上涨显式告警、summary 成本
    变化计数）与新建 tests/test_eval_cost_p1.py；不改 PASS/FAIL 判定；不碰 loader/runner
  - verify: `uv run python -m pytest tests -q -k eval_cost`（成功判定：exit 0 且 cost
    字段、上涨告警文案（「4 turns → 7 turns」式）、下降/持平不告警、不改判定、旧格式
    报告兼容均有测试；失败处理：先查 diff 键的缺省处理）
- [x] task_0004 文档同步（docs/eval.md + README 行）
  - depends-on: task_0002
  - boundary: 只改 docs/eval.md（断言表、schema 说明、报告口径、Scope 一节）与
    README.md 特性表「评测回归」行；以已落盘实现为准
  - verify: `uv run python -m pytest tests -q -k docs_integrity`（成功判定：exit 0
    （机械断言不回归）；内容正确性由 check 轮人工核对断言表与实现一致；失败处理：
    查 test_docs_integrity 的断言面）

## 阶段二：集成验收

- [x] task_0005 全量门与真实入口
  - depends-on: task_0001, task_0002, task_0003, task_0004
  - boundary: 不改业务代码；只允许按门禁结果回修本包各任务的遗漏面（仍限各任务
    boundary 文件）
  - verify: `uv run python -m pytest tests -q && uv run ruff check . && uv run ruff format --check . && uv run nexuscli eval run --cwd /tmp/nexuscli-eval-gate-p1`（前置：仓库根运行；
    成功判定：四段命令全部 exit 0，且 eval run 输出的报告含分档视图与成本字段；
    失败处理：按失败段定位到对应任务 boundary 内修复后重跑）

---

**当前进度**：由脚本计算，无需手动维护
