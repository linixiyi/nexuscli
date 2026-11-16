# mypy 棘轮收紧：清零 7 个存量类型错误 - 任务拆解

## 使用规则

- 每个任务只改 `boundary` 列出的文件；发现相邻问题先记录，不顺手修。
- 机器任务的 `verify` 以首个反引号 span 为执行命令；命令必须在包工作区根目录可执行。
- 修复落在根因（标注/narrowing/协议对齐）；禁止用 `# type: ignore`、`cast` 滥竽充数、扩大 overrides 清单来压错误（棘轮另两个指标会红）。
- 若某处暴露真实运行时 None/类型不一致（A3 假设破裂）：按最小语义修复 + 针对性测试，并在 spec.md `## Decisions` 记一行。
- 行为语义原则上不变：每个任务 verify 前先跑受影响模块的既有测试。

## 阶段 A：分组清错

- [x] task-0001 eval 两文件三处清零
  - id: task-0001
  - boundary: src/nexuscli/eval/loader.py, src/nexuscli/eval/real.py, tests/**
  - origin: planned
  - expect: red
  - behavior: 修复 `eval/loader.py:919`（`MemoryQuerySpec(target=...)` 收到 `Any | None`，按 F1 收紧声明或调用点收窄到 `int`）与 `eval/real.py:427`（`RealEvalScenario`/`RealEvalResult` 传入按基类 `EvalScenario`/`EvalRunResult` 声明的 `_assertion_failures`，按 F2 对齐签名/泛型，不做调用点 cast）。
  - tests: 若为纯标注修复，跑既有 eval 测试防回归；若动了运行时语义（A3 破裂），在 `tests/**` 补针对性测试。
  - verify: `uv run python scripts/check_mypy_ratchet.py 2>&1 | grep -cE "loader\.py:919|real\.py:427" | grep -qx 0 && uv run --extra dev python -m pytest -q tests/test_eval.py tests/test_eval_assertions_p1.py`

- [x] task-0002 llm/factory.py 一处清零
  - id: task-0002
  - boundary: src/nexuscli/llm/factory.py, tests/**
  - origin: planned
  - expect: red
  - behavior: 修复 `llm/factory.py:158`（`str | None` 赋给声明为 `str` 的变量，按 F1 判断是收紧声明还是补 None 分支）。
  - tests: 若为纯标注修复，跑既有 factory/model 相关测试；若动了运行时语义，补针对性测试。
  - verify: `uv run python scripts/check_mypy_ratchet.py 2>&1 | grep -c "factory\.py:158" | grep -qx 0 && uv run --extra dev python -m pytest -q tests/test_model_discovery.py tests/test_model_selector.py`

- [x] task-0003 skill/propose.py 三处清零
  - id: task-0003
  - boundary: src/nexuscli/skill/propose.py, tests/**
  - origin: planned
  - expect: red
  - behavior: 修复 `skill/propose.py:514/528/529`（`Any | dict | None` 上直接 `.get`，补 None 收窄/判空分支或收紧来源标注；若 None 是真实输入路径，按最小语义修复：None 视为空 dict 并保持输出不变）。
  - tests: 若为纯标注修复，跑既有 skill/propose 相关测试；若动了运行时语义，补针对性测试。
  - verify: `uv run python scripts/check_mypy_ratchet.py 2>&1 | grep -c "propose\.py:5" | grep -qx 0 && uv run --extra dev python -m pytest -q tests/test_skill_propose.py`

## 阶段 B：重写 baseline 锁定

- [x] task-0004 重写 baseline 至 errors=0 并全量棘轮验证
  - id: task-0004
  - boundary: mypy-baseline.json
  - depends-on: task-0001, task-0002, task-0003
  - origin: planned
  - expect: red
  - behavior: 运行 `uv run python scripts/check_mypy_ratchet.py --write-baseline` 把实测写回 `mypy-baseline.json`；确认 `errors` 为 0、`ignored_modules` ≤ 30、`ignored_lines` ≤ 6、mypy/python 版本字段不变。不改脚本与 pin。
  - tests: 无新增测试；以棘轮与断言命令自证。
  - verify: `uv run python scripts/check_mypy_ratchet.py && python3 -c "import json; b=json.load(open('mypy-baseline.json')); assert b['errors']==0 and b['ignored_modules']<=30 and b['ignored_lines']<=6, b"`

---

**当前进度**：由脚本计算，无需手动维护
- [x] task-0005 runner.py 协议对齐签名收编
  - id: task-0005
  - boundary: src/nexuscli/eval/runner.py
  - depends-on: task-0001
  - origin: emergent
  - expect: green
  - behavior: 把 D1 已实施的最小协议对齐改动（`_AssertionScenario` / `_AssertionResult` 两个结构面 Protocol、`_assertion_failures` 签名改用它们、`EvalAssertions` 导入）收编为正式任务边界；零运行时行为变化；loader 的两轨类型分离设计（docstring「Deliberately a different type」）保持不动。
  - verify: `uv run python scripts/check_mypy_ratchet.py 2>&1 | grep -cE "loader\.py:919|real\.py:427|runner\.py:" | grep -qx 0 && uv run --extra dev python -m pytest -q tests/test_eval.py tests/test_eval_assertions_p1.py`
