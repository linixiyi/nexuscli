# check 轮发现与处置（2026-10-27）

来源：实现工作流内的独立评审员（1 条，unconfirmed）+ 主会话验收核实。

## R1 README「七种轨迹断言」措辞与实现术语冲突
- 评审员报 unconfirmed；主会话核实：README.md:43 确实把七种断言统称「轨迹断言」，
  而 loader.py 模块 docstring 口径「轨迹断言」仅指序列类（expected_tool_sequence/
  required_tools）。
- 处置：**resolved_current**。改为「七种断言（轨迹序列/子集/禁用工具/回合与调用数
  上限）」；docs/eval.md 的「seven supported checks」措辞无冲突，保留。

## 实现报备的 boundary 偏差（均为满足集成验收的必要前提，主会话核可）
- task_0001/0003/0002 均触及 tests/test_eval.py：三处「内置场景数 = 3」硬编码改 4、
  summary 精确等值断言补 cost/difficulty 键——新字段与新场景使等值断言必然失效，
  属最小必要改动，非边界越权。
- task_0002 触及 runner.py（EvalRunResult 加 difficulty 字段并随 run_scenario 复制）：
  build_report 只拿 EvalRunResult，分档必须随结果携带；带默认值，既有构造点不受影响。
  此改动同时在 task_0001 的 runner.py boundary 内（_assertion_failures 同文件），
  两实现者经工作流分相位（∥ → 串行）避免了同文件并发写。
- src/nexuscli/eval/__init__.py 导出扩展（DIFFICULTY_LEVELS 等）：导出面最小增量。
- 处置：**resolved_current**（回写本文件即认）；未触碰 spec §5「本轮不应触碰」
  清单的任何语义面（runner 主循环/既有断言语义/报告文件名兼容）。

## notCovered 项的验收口径
- 「成本 diff 真实两连跑」：tests/test_eval_cost_p1.py 的 CliRunner 两轮用例 +
  主会话验收对同一 /tmp 目录真实两连跑（第二轮 cost_unchanged=4、无告警）——已覆盖。
- 「分档视图人工目检」：验收输出 `difficulty: short ... | medium total=4 ... | long ...`
  一行呈现，报告 JSON summary.difficulty 三档结构完整——已目检通过。
