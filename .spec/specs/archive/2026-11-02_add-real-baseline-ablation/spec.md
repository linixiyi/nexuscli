# real 轨 baseline 消融：agent vs 裸模型 harness 增益报告 - 项目范围

<!-- spec-format: 2 -->

## 1. 问题定义
- **项目目标**：`nexuscli eval run --real --baseline`——同一场景集跑两个对象：①agent
  （现状：14 项只读白名单工具 + skill 候选注入 + 完整 harness）②裸模型（同 LLM 直连单次
  补全，无工具无 skill 注入），产出对比报告：每场景与汇总的 harness 增益（pass 差值、
  token 差值），对比文件独立落盘。
- **目标用户**：要回答「harness 到底带来多少净提升」的维护者；nightly 报告读者。
- **核心价值**：把「agent 得分」升级为「agent 相对裸模型的增益」——这是评测方法论里
  最有说服力的数字（裸模型 = 同模型不挂 harness 的下界）。

## 2. 假设与待确认

### 2.1 已确认事实
- real 轨执行：run_real_scenarios（real.py）逐场景跑 agent，报告写 last-real-report.json；
  2026-11-02_add-real-eval-suite 已提供 30 题校准套件（agent 侧基线 24/30 = 80%）
- 断言七键中 final_contains 与 judge 只依赖最终文本——对裸模型可用；工具序列类断言
  （required/forbidden/sequence/调用数）对无工具的裸模型无意义
- 裸模型形态决策原料：create_llm_client 直连、单次 chat 补全、无工具注册、无 skill 候选、
  无 agent 系统提示（最小 system：直接回答）
- spec_verify 600 秒单命令上限（包 C 实证）——30 题 baseline 全量超限，真网关验收沿用
  「主会话执行 + verify 断言」模式
- last-real-report.json 契约已被下游消费（propose 读 real 轨报告、post-landing diff）——
  对比数据必须独立文件，不改既有报告 schema

### 2.2 关键假设
- 裸模型单次补全即可答题（场景题面自包含；需要工具的题裸模型天然答不出——这正是消融
  要暴露的差距，不是缺陷）
- baseline 模型与 agent 模型相同（同一配置链同 model）——增益才归因于 harness 而非模型差
- 对比口径：per-scenario agent_status/baseline_status、双方 tokens、delta；汇总 pass 率
  双列 + 差值 + 「agent 独过/裸模型独过/双过/双败」四象限计数

### 2.3 待确认问题
- [x] baseline 断言子集里 max_turns 怎么办？→ 结论：不适用（单次补全无回合概念），
  对比报告标注 N/A；场景题面不改

### 2.4 可选解释与取舍
- 当前选择：--baseline 是一次性双跑（agent + baseline 串行各一遍）而非缓存 agent 侧
  结果 -> 理由：模型输出有波动，同批双跑才是公平对比；成本 = 2 × 全量
- 当前选择：对比文件 .nexuscli/evals/last-real-comparison.json 独立 -> 理由：不破坏
  last-real-report.json 既有 schema 与消费方
- 当前选择：baseline 无 judge 重用场景内 judge 配置（同 rubric 同阈值）-> 理由：同一把尺

### 2.5 已证伪路径
- 无（暂无证伪记录）

## 3. 功能范围

### 3.1 核心功能（MVP）
- [x] 裸模型执行路径：同场景 prompt → 单次补全（无工具/skill/agent 提示），记录 usage，
  按 final_contains/judge 子集判 PASS/FAIL
- [x] `eval run --real --baseline`：双对象串行全跑 + last-real-comparison.json（per-scenario
  delta + 四象限 + 汇总双列）；无 Key 报错语义与 --real 一致
- [x] 测试全 hermetic（假 LLM 双侧注入）：对比计算两侧、四象限、断言子集（工具类断言
  对 baseline 不判）、独立文件不污染 last-real-report.json、无 Key、judge 重用

### 3.2 扩展功能
- 无

### 3.3 不在范围内
- hermetic 轨与 last-real-report.json schema 改动；--repeat 与 baseline 组合（后续需要再排）
- SOTA 锚定（外部 Codex/CC CLI 对比——依赖外部授权，挂账）
- 成本换算差值（币种折算留 usage 层，对比只报 token 与 pass）

## 4. 最小实现路径
- 最简单可行方案：real.py 新增 run_baseline_scenario（单补全 + 子集断言）与
  run_baseline_comparison（双跑 + 对比装配）；CLI --baseline 旗标接线
- 暂不引入：baseline 侧重试/并发、对比历史趋势、多 baseline（如不同裸 prompt 模板）
- 不做的抽象/配置化：不做「对比对象」插件接口

## 5. 技术决策
- 技术栈：Python 3.11+ / uv / pytest，零新依赖
- 证据协议：evidence: runs（机器任务 verify 命令由 scripts/spec_verify.py run 真实执行
  并记录 runs.jsonl；无命令任务写 `verify: manual: <理由>`；verify 命令不得自引用本包
  check 门禁）
- 本轮允许改动：src/nexuscli/eval/real.py（新增执行路径）、src/nexuscli/entrypoints/cli.py
  eval 命令组（--baseline 旗标）、tests 新增、docs/eval.md、README 评测回归行
- 本轮不应触碰：last-real-report.json schema、hermetic 轨、场景文件、memory/telemetry
- Git integration branch：`spec/2026-11-02_add-real-baseline-ablation`

### 5.1 验证策略
- 范围级别：package
- 变更对象：eval/real.py 新增路径、CLI 旗标、eval 测试、docs/eval.md
- 快速检查：`uv run --extra dev python -m pytest -q -k eval`
- 集成检查：`uv run --extra dev python -m pytest -q`（real.py 被 propose/场景消费，全量兜底）
- 全项目检查：适用外：本轮不涉及共享基础设施、跨模块契约或发布载荷；closeout 由发布
  门禁统一执行
- 升级触发：若必须改 run_real_scenarios 既有签名才能复用，停下升级并记录理由

### 5.4 编排策略
- route: local
- 依据：route_decision.py = package-structure，score 0
- 执行说明：task_0001/0002 由主会话委托动态工作流子代理实现（用户显式要求）；task_0003
  真网关验收按 AGENTS.md「执行环境与冒烟」守则由主会话执行（600 秒上限 → 主会话跑
  双跑本体、verify 断言对比文件）

## 6. 成功标准与验证方式
- [x] 裸模型路径与 --baseline 双跑有全路径测试（对比两侧/四象限/断言子集/独立文件/
  无 Key/judge 重用） -> verify: task_0001
- [x] docs/eval.md 对比口径 + README 行，全量与 Ruff 双检全绿 -> verify: task_0002
- [x] 真网关 30 题双跑产出 last-real-comparison.json，四象限与增益列完整，last-real-
  report.json 为 agent 侧全量结果（schema 未变） -> verify: task_0003

## 7. 风险与约束
- 风险点：双跑 token 成本（约 50-90 万） -> 缓解：一次性验收成本，本地网关；对比文件
  落盘后无需重跑
- 风险点：裸模型把「无法调用工具」的题胡编 -> 缓解：断言子集如实判定为 FAIL——这正是
  消融要测的差距；报告四象限让「双败题」可见
- 风险点：对比文件与主报告消费方混淆 -> 缓解：独立文件名 + 文档明示 propose/post-landing
  仍读 last-real-report.json
