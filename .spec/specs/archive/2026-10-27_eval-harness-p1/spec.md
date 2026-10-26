# 离线 eval harness 三项增强 - 项目范围

<!-- spec-format: 2 -->

## 1. 问题定义
- **项目目标**：给脚本化假 LLM 驱动真实 Agent 的离线回归 harness（src/nexuscli/eval/）
  做三项增量：轨迹断言增强（三种新断言）、场景难度分档、成本确定性指标（turns 与
  工具调用数进报告并对上次报告 diff 告警）。
- **目标用户**：把 eval 当 CI 回归门禁的本仓开发者。
- **核心价值**：回归报告从「过/没过」升级为「断言表达力够用 + 退化能定位到难度档 +
  效率退化可见」。

## 2. 假设与待确认

### 2.1 已确认事实
- 现有断言四种：expected_tool_sequence / final_contains / forbidden_tools / max_turns
  （loader.py EvalAssertions 与 _parse_assertions、runner.py _assertion_failures）
- EvalRunResult 已有 turns 与 tool_sequence 字段，报告未消费
- 真实入口 `uv run nexuscli eval run`（cli.py:574-615）：报告写
  `<cwd>/.nexuscli/evals/last-report.json`，下次运行读取做 pass->fail / fail->pass diff，
  任一场景失败 exit 1
- 现有内置场景 3 个（examples/evals/01-03*.json）

### 2.2 关键假设
- 三种新断言可在现有 _assertion_failures 的判定结构内表达，不需要改 runner 主循环
- difficulty 三档（short/medium/long）足够承载「退化集中在哪一档」的定位需求

### 2.3 待确认问题
- [x] 成本上涨是否改 PASS/FAIL？→ 结论：不改，只做可见告警（用户指令明确）

### 2.4 可选解释与取舍
- 当前选择：required_tools 用子集语义而非「按序出现」 -> 精确序列对真实回归太脆
  （用户指令）；max_tool_calls 与 agent 循环调用检测互补，可写「同参重复调 3 次必须
  被拦截」类场景
- 当前选择：成本口径 = turns + total_tool_calls（len(tool_sequence)）-> 离线无 token，
  这两个量完全确定，是 HAL 成本控制的脚本化映射

### 2.5 已证伪路径
- 无（暂无证伪记录；条目写：方案概述 + 证伪依据出处 + 日期）

## 3. 功能范围

### 3.1 核心功能（MVP）
- [x] 轨迹断言增强：required_tools（子集无序）/ max_tool_calls（单工具次数上限）/
  max_total_tool_calls（总数上限）三种断言落地，schema 严格校验（未知键、错误形状
  报 EvalSchemaError 并指名文件），失败原因 human-readable；examples/evals/ 补
  ≥1 个覆盖新断言的内置场景
- [x] 场景难度分档：scenario schema 可选 difficulty 字段（严格校验 short/medium/long），
  reporter 按档聚合通过/失败数，summary 有分档视图（能显示退化集中在哪一档）
- [x] 成本确定性指标：build_report 每场景条目写 turns 与 total_tool_calls，与上次
  报告 diff，上涨显式告警（「4 turns → 7 turns」式），summary 给成本变化计数；
  不改 PASS/FAIL 判定

### 3.2 扩展功能
- 无

### 3.3 不在范围内
- 用户模拟场景、记忆召回评测、真模型轨道、许可变更（挂账后续包）
- 不改 runner 主循环结构与 agent 本体；不动 docs/eval.md 之外的文档（README 特性表
  「评测回归」行除外）

## 4. 最小实现路径
- 最简单可行方案：三种断言进现有 EvalAssertions/_parse_assertions/_assertion_failures
  三点；difficulty 进 schema + reporter 聚合；成本字段进 build_report/format_report
- 暂不引入：断言 DSL/表达式语言、按工具参数维度的断言、分档权重、成本预算门槛
  （成本只告警不判定）
- 不做的抽象/配置化：不把分档做成可配置枚举（固定三档）、不做自定义报告模板
- 全部保持 hermetic：脚本化假 LLM、stub 工具、无网络、无真实 token

## 5. 技术决策
- 技术栈：纯 Python 标准库 + pytest（与 eval 模块现状一致）
- 证据协议：evidence: runs（机器任务 verify 命令由 scripts/spec_verify.py run 真实执行
  并记录 runs.jsonl；无命令任务写 verify: manual: <理由>；verify 命令不得自引用本包
  check 门禁）
- 本轮允许改动：src/nexuscli/eval/**、examples/evals/**、tests/test_eval_*_p1.py
  （新建三个测试文件）、docs/eval.md、README.md 特性表「评测回归」一行
- 本轮不应触碰：runner.py 主循环结构、agent/engine 本体、loader 既有四种断言语义、
  报告文件名与 last-report.json 的兼容性（旧报告无新字段时 diff 侧按缺省处理）
- Git integration branch：`spec/2026-10-27_eval-harness-p1`

### 5.1 验证策略
- 范围级别：package
- 变更对象：eval 模块三文件、内置场景、三个新测试文件、两处文档
- 快速检查：`uv run python -m pytest tests -q -k eval`
- 集成检查：`uv run nexuscli eval run --cwd /tmp/nexuscli-eval-gate-p1`（真实入口
  产出含分档与成本字段的报告，exit 0）
- 全项目检查：适用外：本轮不涉及共享基础设施、跨模块契约或发布载荷；由发布门禁统一执行
- 升级触发：只有改动共享基础设施、跨模块契约、构建/依赖、发布载荷，或
  package/integration 检查暴露跨边界回归时，才把范围升级并记录理由

## 6. 成功标准与验证方式
- [x] 三种新断言落地且有测试与内置场景覆盖 -> verify: task_0001
- [x] difficulty 分档落地且 summary 有分档视图 -> verify: task_0002
- [x] 成本指标进报告且上涨告警、不改判定 -> verify: task_0003
- [x] 文档同步（断言表/schema/Scope/README 行）-> verify: task_0004
- [x] 全量 pytest 绿 + ruff 双绿 + eval run 真实入口产出含分档与成本字段的报告 -> verify: task_0005

## 7. 风险与约束
- 风险点：last-report.json 兼容性——旧报告无 cost/difficulty 字段时 diff 崩溃 ->
  缓解：diff 侧按缺省处理（旧报告无字段则跳过该项 diff），测试覆盖旧格式输入
- 风险点：新断言与现有四种的判定优先级/文案风格不一致 -> 缓解：实现前先读
  _assertion_failures 现有文案结构，测试断言具体文案片段

### 5.4 编排策略
- route: review
- immediate blocker: 任务间文件冲突仲裁（loader.py 双任务接入顺序）与最终验收留在主线程
- ownership: 任务一（loader+runner+断言测试+场景）∥ 任务三（reporter+成本测试）并行
  in-process 子代理；任务二在两者之上集成（loader difficulty + reporter 聚合）串行；
  文档同步单独一切片；独立评审只读
- waiting strategy: 任务二等任务一/三落地；评审发现走有界修复循环（≤3 轮）后进全量门
- verification gate: 全量 pytest + ruff 双绿 + eval run 真实入口产出含分档与成本字段
  的报告；「数据迁移」风险 = last-report.json 旧格式兼容，由任务三测试覆盖并经
  check 轮 reviewer 复核
