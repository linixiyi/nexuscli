# 真模型 eval 轨道：pass^k + 真实成本 + LLM-as-judge - 项目范围

<!-- spec-format: 2 -->

## 1. 问题定义
- **项目目标**：在离线 eval harness 旁新开一条真 LLM 评测轨道：同一场景连跑 k 次报全通过率
  （pass^k，测稳定性而非运气）、每场景记录真实 token 成本、新增 LLM-as-judge 断言支持
  开放式产出的评分。定位 nightly/手动跑，不进 CI 门禁。
- **目标用户**：要回答「真实模型 + 真实 prompt 表现如何、花了多少钱、稳定吗」的本仓开发
  者与维护者。
- **核心价值**：填补 docs/eval.md 自我声明的边界（「不测真实模型质量，成本延迟在范围
  外」）——hermetic 轨道守接线，真模型轨道看质量与成本。

## 2. 假设与待确认

### 2.1 已确认事实
- 现有 eval 全部 hermetic：ScriptedLlmClient 重放 scripted_turns、stub 工具、无网络、
  telemetry 关闭（docs/eval.md「How a scenario runs」）；真实入口 `nexuscli eval run`
  （cli.py eval 命令组），报告写 .nexuscli/evals/last-report.json 并做 regression diff
- 断言现有 7 种 + 记忆排序断言（memory_eval 场景），场景 schema 严格校验
  （EvalSchemaError 指名文件），内置场景 9 个（examples/evals/01-08）
- usage 管线已有：流式请求带 stream_options.include_usage、解析 cache/reasoning token、
  usage 落库（usage_store）与 `--json` 机器可读口径；配置链支持 DeepSeek/GLM/Kimi/Step/
  Anthropic/OpenAI-compatible，缺 Key 时请求会失败而非静默
- eval 路径经 _eval_config 构造 NexusCliConfig（telemetry/审计关闭）——真模型轨道需要
  一条不关 usage 采集的配置变体
- 环境探测：~/.nexuscli/config.json 存在（246B），shell 环境无明文 Key 变量、项目无 .env；
  真实 Key 可用性运行时才可知

### 2.2 关键假设
- 真模型轨道与 hermetic 轨道共享场景 schema 的断言子集（final_contains/max_turns/
  forbidden_tools/required_tools/judge），但 expected_tool_sequence 对真实 LLM 过脆，
  文档标注不推荐而非禁用
- judge 断言的评审模型调用走与被测 agent 相同的客户端工厂（OpenAI-compatible），评审
  模型可按场景覆盖，缺省跟随当前 provider/model
- 真模型轨道的报告独立成文件（.nexuscli/evals/last-real-report.json），绝不污染
  hermetic 基线的 regression diff
- 测试全程用可注入的假 LLM/假 judge（复用 ScriptedLlmClient 注入模式）保持 hermetic；
  真实网络只在显式 --real 冒烟时发生

### 2.3 待确认问题
- [x] 真模型冒烟在本环境能否作为机器 verify？→ 结论：Key 可用性运行时才可知且涉及
  真实计费，verify 记 manual（理由见 tasks.md task_0004），工作流做机会性冒烟并留证据
  备注，不进门禁

### 2.4 可选解释与取舍
- 当前选择：真模型轨道用独立最小场景集（examples/evals/real/*.json），不强行复用
  hermetic 场景 -> 理由：hermetic 场景的 stub 工具对真模型无意义，真模型场景需要真实
  只读工具面
- 当前选择：pass^k 语义 = 同一场景 k 次全部 PASS 的比率（k 次内逐次结果全保留）->
  理由：agent 产品要的是稳定性口径；单次 PASS/FAIL 仍逐条可见
- 当前选择：真模型轨道只挂只读 builtin 工具子集 + 权限按 -p 语义失败关闭 -> 理由：
  无人值守评测绝不能有写副作用

### 2.5 已证伪路径
- 无（暂无证伪记录）

## 3. 功能范围

### 3.1 核心功能（MVP）
- [x] `nexuscli eval run --real --repeat k`：真模型轨道入口——真实 LLM 驱动真实只读工具
  面，逐场景逐次记录结果与 token 用量，无 Key 时明确报错退出（不静默跳过）
- [x] pass^k 聚合与真实成本口径：报告含逐次结果、all-pass 率、每场景平均 token/成本与
  总消耗，写 last-real-report.json
- [x] judge 断言：scenario 内 rubric + 评审模型打分阈值判定，评审模型可覆盖；假 judge
  注入路径全程可测

### 3.2 扩展功能
- 无

### 3.3 不在范围内
- hermetic 轨道的行为改动（回归基线零影响）
- 定时任务/夜间调度接入（cron 已有，接线后续按需）
- 并发跑重复轮次（k 次串行即可）、结果缓存、多评审模型投票

## 4. 最小实现路径
- 最简单可行方案：eval 模块内新增 real 轨道执行路径（与 hermetic 分派并列），复用
  ToolExecutor/权限链/usage 事件；CLI 在 eval run 上加 --real/--repeat 两个旗标
- 暂不引入：新依赖、评审模型独立配置体系（只做场景级 model 覆盖）、成本重试/预算熔断
- 不做的抽象/配置化：不为 pass^k 做独立命令（--repeat 足够）；不为真模型轨道做
  regression diff（nightly 对比靠人工看报告）

## 5. 技术决策
- 技术栈：Python 3.11+ / uv / pytest，零新依赖
- 证据协议：evidence: runs（机器任务 verify 命令由 scripts/spec_verify.py run 真实执行
  并记录 runs.jsonl；无命令任务写 `verify: manual: <理由>`；verify 命令不得自引用本包
  check 门禁）
- 本轮允许改动：src/nexuscli/eval/**、src/nexuscli/entrypoints/ 的 eval 命令组、
  examples/evals/real/**、tests 中 eval 相关、docs/eval.md、README 特性表评测回归行
- 本轮不应触碰：hermetic 轨道既有行为与报告格式、agent 核心、memory、telemetry、
  其他 CLI 命令
- Git integration branch：`spec/2026-10-27_add-real-model-eval-track`

### 5.1 验证策略
- 范围级别：package
- 变更对象：eval 模块（real 轨道）、eval CLI 命令、real 场景示例、eval 测试、docs/eval.md
- 快速检查：`uv run --extra dev python -m pytest -q -k eval`
- 集成检查：`uv run --extra dev python -m pytest -q`（real 轨道与 hermetic 共享
  loader/reporter 面，全量兜底）
- 全项目检查：适用外：本轮不涉及共享基础设施、跨模块契约或发布载荷（eval 内部新增
  轨道 + CLI 单命令旗标）；closeout 由发布门禁统一执行
- 升级触发：若实现发现必须改 agent 核心或 usage 管线才能完成，停下升级范围并记录理由

### 5.4 编排策略
- route: local
- 依据：route_decision.py = package-structure，score 0，无强结构信号一律 local
- 执行说明：实现段由主会话委托动态工作流的子代理完成（用户显式要求），主会话保留
  路由、验收、done/push 门禁；实现子代理一次一个任务串行推进

## 6. 成功标准与验证方式
- [x] --real 轨道可运行、无 Key 明确报错、只读工具面与失败关闭权限生效 ->
  verify: task_0001
- [x] pass^k 聚合、真实成本记录、judge 断言（含阈值判定与评审模型覆盖）有测试证明 ->
  verify: task_0002
- [x] 全量测试、Ruff 双检全绿，docs/eval.md 两轨道口径分开且 README 同步 ->
  verify: task_0003

## 7. 风险与约束
- 风险点：真模型冒烟消耗真实额度 -> 缓解：不进门禁、机会性执行且只跑最小场景 1 轮；
  报告展示成本让人看得见花了多少
- 风险点：真模型无人值守产生写副作用 -> 缓解：只读工具子集 + 失败关闭权限 + 审计照常
- 风险点：judge 评分不稳定 -> 缓解：rubric 结构化、阈值场景内声明、测试用假 judge 定点
  覆盖，不追求评审模型间一致性
