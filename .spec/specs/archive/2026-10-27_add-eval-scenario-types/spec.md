# eval 新场景类型：多轮用户模拟与记忆召回评测 - 项目范围

<!-- spec-format: 2 -->

## 1. 问题定义
- **项目目标**：给离线 eval harness（src/nexuscli/eval/）新增两类场景类型——多轮用户模拟
  （τ²-bench 式：脚本化用户与 agent 多轮对抗，断言业务策略遵从）与记忆召回评测
  （hermetic seed MemoryManager 后断言 recall 排序）。
- **目标用户**：把 eval 当 CI 回归门禁的本仓开发者；记忆召回质量的验收者。
- **核心价值**：「策略遵从 + 用户对抗」与「记忆召回质量」两类目前完全测不了的行为纳入
  离线回归；记忆评测场景同时是已归档包 2026-10-27_memory-multi-signal-recall
  （多信号召回）的持续性回归门禁。

## 2. 假设与待确认

### 2.1 已确认事实
- 断言现有 7 种：expected_tool_sequence / final_contains / forbidden_tools / max_turns +
  required_tools / max_tool_calls / max_total_tool_calls（loader.py EvalAssertions、
  runner.py _assertion_failures）；difficulty 分档与成本指标（turns、total_tool_calls）
  已进报告（P1 包 2026-10-27_eval-harness-p1 已归档）
- scenario schema 严格校验：未知键/错误形状报 EvalSchemaError 并指名文件
- ScriptedLlmClient（runner.py:46 附近）把 scripted_turns 重放为流式事件协议；
  纯 text turn 结束 run
- MemoryManager（memory/manager.py）构造函数可注入 signals 序列
  （memory/signals.py，default_signals = lexical+BM25+entity）与 DB 路径——hermetic
  评测可用临时 DB 直接构造，不需要走 agent loop
- eval 运行 hermetic：telemetry/审计关闭、无网络（docs/eval.md「How a scenario runs」）
- 现有内置场景 examples/evals/01-03*.json 共 3 个

### 2.2 关键假设
- 多轮用户模拟可在 eval runner 层实现：agent 产出最终文本后由 runner 注入下一条用户
  消息、继续同一 agent 会话再跑，不需要改 src/nexuscli/agent/ 核心循环
- 记忆召回评测不走 agent loop：场景直接构造 MemoryManager（临时 DB）+ 断言 recall
  排序，实现为独立执行路径，报告口径（PASS/FAIL、regression diff）与现有场景一致
- schema 新键全部可选，现有内置场景（01-03）零改动可跑

### 2.3 待确认问题
- [x] 多轮场景 schema 形态：用户消息并入现有 scripted 结构还是新顶层键？→ 结论：执行时
  按最小改动决定（现有 scripted_turns 语义保持向后兼容），形态回写 completion-summary

### 2.4 可选解释与取舍
- 当前选择：多轮轮数硬上限（场景级 rounds 上限 + 复用 max_turns 语义）-> 理由：防止
  用户脚本与 agent 互相喂话无限循环
- 当前选择：记忆评测不引入真实 embedding -> 理由：hermetic 与确定性优先；向量信号在
  memory 侧本就可插拔，评测只测默认三路信号

### 2.5 已证伪路径
- 无（暂无证伪记录）

## 3. 功能范围

### 3.1 核心功能（MVP）
- [x] 多轮用户模拟场景：schema（多轮用户消息段落 + 结束标记 + 轮数上限）+ runner 多轮
  驱动 + 跨轮断言 + ≥2 个内置示例（其一为策略场景：用户索要退款 → agent 必须先查订单
  （required_tools），全程不得直接调 refund（forbidden_tools））
- [x] 记忆召回评测场景：seed 记忆（目标条目 + 干扰条目）→ 断言目标进前 K、干扰不排前
  + ≥2 个内置示例

### 3.2 扩展功能
- 无

### 3.3 不在范围内
- 真模型 eval 轨道（pass^k / 真实成本 / LLM-as-judge）——后续包
- OTLP 导出、skill 自进化——后续包
- src/nexuscli/agent/ 与 memory 语义的任何改动

## 4. 最小实现路径
- 最简单可行方案：多轮逻辑全部收在 eval/runner.py 的场景执行层（轮循环 + 同会话注入
  用户消息）；记忆评测走独立轻量执行路径，复用 reporter 的 PASS/FAIL 与 regression
  diff 结构
- 暂不引入：真实 LLM、embedding、任何新依赖
- 不做的抽象/配置化：不为「用户人格」建模（用户脚本就是数据）；不把多轮逻辑泛化成
  通用步骤图（已有 /workflow 引擎）

## 5. 技术决策
- 技术栈：Python 3.11+ / uv / pytest，零新依赖
- 证据协议：evidence: runs（机器任务 verify 命令由 scripts/spec_verify.py run 真实执行
  并记录 runs.jsonl；无命令任务写 `verify: manual: <理由>`；verify 命令不得自引用本包
  check 门禁）
- 本轮允许改动：src/nexuscli/eval/**、examples/evals/**、tests 中 eval 相关文件、
  docs/eval.md、README 特性表「评测回归」行
- 本轮不应触碰：src/nexuscli/agent/**、src/nexuscli/memory/**（只读使用）、CLI 其他
  命令、frontend、telemetry
- Git integration branch：`spec/2026-10-27_add-eval-scenario-types`

### 5.1 验证策略
- 范围级别：package
- 变更对象：eval 模块（loader/runner/reporter）、examples/evals、eval 测试、docs/eval.md
- 快速检查：`uv run --extra dev python -m pytest -q -k eval`
- 集成检查：`uv run --extra dev python -m pytest -q`（eval 与 memory/prompt 召回共享面，
  全量回归兜底）
- 全项目检查：适用外：本轮不涉及共享基础设施、跨模块契约或发布载荷（eval 为独立模块面）；
  closeout 由发布门禁统一执行
- 升级触发：若实现发现必须改 memory/agent 内部才能完成，停下升级范围并记录理由

### 5.4 编排策略
- route: local
- 依据：route_decision.py = package-structure，score 0，无强结构信号一律 local
- 执行说明：实现段由主会话委托动态工作流的子代理完成（用户显式要求），主会话保留路由、
  验收、done/push 门禁；实现子代理一次一个任务串行推进，避免双写

## 6. 成功标准与验证方式
- [x] 多轮用户模拟场景经真实入口可跑通，跨轮策略断言（required_tools +
  forbidden_tools）生效 -> verify: task_0001
- [x] 记忆召回评测场景可跑通，排序断言（目标进前 K、干扰不排前）生效 -> verify: task_0002
- [x] 全量测试、Ruff check/format、真实入口三项全绿，docs/eval.md 与 README 与实现
  同步 -> verify: task_0003

## 7. 风险与约束
- 风险点：多轮注入与 agent 循环调用检测相互作用导致场景难收敛 -> 缓解：轮数硬上限 +
  示例场景先小后大
- 风险点：MemoryManager 临时 DB 路径处理不当写穿真实用户目录 -> 缓解：conftest 已
  autouse 重定向 HOME/USERPROFILE，评测内再显式用 pytest tmp 路径，测试断言 DB 文件
  落在临时目录
