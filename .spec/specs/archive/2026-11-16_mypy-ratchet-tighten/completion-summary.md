# 2026-11-16_mypy-ratchet-tighten - Completion Summary

## Delivery
- task-0001 eval 两文件三处清零
- task-0002 llm/factory.py 一处清零
- task-0003 skill/propose.py 三处清零
- task-0004 重写 baseline 至 errors=0 并全量棘轮验证
- task-0005 runner.py 协议对齐签名收编

## Evidence
- Final-tree verifies passed; see runs.jsonl.

## Decisions
- D1 |  real.py:427 按 F2 pick a「协议对齐」修：`_assertion_failures` 签名改为结构面 Protocol（`_AssertionScenario.assertions` / `_AssertionResult.{tool_sequence,final_text,turns}`）。F2 的前提（"RealEvalScenario/RealEvalResult 与基类是继承但 mypy 不认"）与事实不符——loader.py docstring 明言两类型「Deliberately a different type」（跨轨互传是设计禁区），继承会破坏该设计且 EvalScenario 多数字段必填；调用点 cast 被 F2 明令禁止。协议签名唯一落在 runner.py（task-0001 boundary 外），故记此决定：runner.py 仅改导入 + 两个小 Protocol + 签名标注，零运行时行为变化 | by: V1 | reversal: reversible

## Parked

## Emergent tasks
- task-0005 runner.py 协议对齐签名收编

## Lessons
- See .spec/lessons.md for retained project lessons.
- Archived: 2026-11-16T16:06:25+00:00

## Git
- Commit interval: 592ee84ba46d46900f4056f39df867bf7112e8ef
