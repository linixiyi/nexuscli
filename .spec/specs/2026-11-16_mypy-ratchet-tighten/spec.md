<!-- spec-format: 3 -->

# mypy 棘轮收紧：清零 7 个存量类型错误 - 项目范围

## Goal

- goal: 修复 mypy baseline 里的全部 7 个存量类型错误并把 baseline `errors` 写回 0；修复必须落在根因（类型标注/narrowing/协议对齐），不新增 `# type: ignore`、不往 `[tool.mypy.overrides]` 排除清单塞模块（另两个棘轮指标不允许变多）。
- literal ask: 「mypy 棘轮收紧」——用户 2026-11-16 会话批准的改进包。
- stated purpose: `mypy-baseline.json` 记录 errors=7（eval/loader.py:919、llm/factory.py:158、eval/real.py:427×2、skill/propose.py:514/528/529）；棘轮只防变差不促变好，存量错误需要一次性清偿并重写 baseline 锁定成果。
- done means: `uv run python scripts/check_mypy_ratchet.py` 通过且 `mypy-baseline.json` 的 `errors` 字段为 0、`ignored_modules` ≤ 30、`ignored_lines` ≤ 6；7 处错误全部消失；受影响模块的既有测试不回归。

## Charter

- V1 priority: quality > maintainability > speed > cost | charter.md
- V2 scope: strict | charter.md
- V3 verification: package | charter.md
- V4 finish: commit | charter.md
- V5 dependencies: none-new | charter.md
- V6 compatibility: keep-public-api | charter.md
- V7 blocked: park-and-continue | charter.md

## Scope

- in: src/nexuscli/eval/loader.py, src/nexuscli/eval/real.py, src/nexuscli/llm/factory.py, src/nexuscli/skill/propose.py, mypy-baseline.json, tests/**
- out: pyproject.toml（mypy pin 与 overrides 不动）, scripts/check_mypy_ratchet.py, src/nexuscli/tools/**, src/nexuscli/entrypoints/**

## Assumptions

- A1 | 当前 baseline errors=7，具体为 eval/loader.py:919（arg-type）、llm/factory.py:158（assignment）、eval/real.py:427（arg-type×2）、skill/propose.py:514/528/529（union-attr×3） | fact | probe: `uv run python scripts/check_mypy_ratchet.py` | CONFIRMED | if false: 以实测错误清单重新分组任务
- A2 | ratchet 脚本无论通过与否都回显 mypy 原始错误明细，可对外部 grep 计数 | fact | probe: `uv run python scripts/check_mypy_ratchet.py 2>&1 | grep -cE "loader\.py:919"` | CONFIRMED | if false: 改用钉版本 mypy 直跑作为 verify
- A3 | 7 处均为标注/narrowing 层面问题，行为语义不需要改变；若个别位置暴露真实 None 路径，按最小语义修复并补针对性测试 | assumption | UNVERIFIED | if false: 该处按行为修复处理，任务内补测试并在 Decisions 记录
- A4 | baseline 由 `uv run python scripts/check_mypy_ratchet.py --write-baseline` 按实测重写，版本 pin 不变 | fact | probe: `head -20 scripts/check_mypy_ratchet.py` | CONFIRMED | if false: 按 README 口径重置 baseline

## Forks

- F1 | when: 某处错误最干净的修法是收紧类型声明本身（如把 `str` 改 `str | None`） | a) 收紧声明（调用方已保证） b) 局部 narrowing/断言 | pick: a | by: V1 | reversal: reversible
- F2 | when: real.py:427 的 RealEvalScenario/RealEvalResult 与基类关系是继承但 mypy 不认 | a) 修基类泛型/协议签名 b) 调用点显式收窄 | pick: a | by: V1 | reversal: reversible

## Criteria

- C1 | eval/loader.py:919 与 eval/real.py:427（两处）从 mypy 输出消失 | task-0001
- C2 | llm/factory.py:158 从 mypy 输出消失 | task-0002
- C3 | skill/propose.py:514/528/529 从 mypy 输出消失 | task-0003
- C4 | ratchet 通过且 baseline errors==0、ignored_modules ≤ 30、ignored_lines ≤ 6 | task-0004

## Decisions

## Parked

## Binding
- branch: `spec/2026-11-16_mypy-ratchet-tighten`
- frozen: 2026-11-16T15:50:24+00:00
