# eval 当 probe 的 skill 自进化最小闭环（提案+人审） - 项目范围

<!-- spec-format: 2 -->

## 1. 问题定义
- **项目目标**：把 `/skill evolve` 从「人手写规则追加」升级为「eval 报告驱动的提案 + 人审落地」
  最小闭环：读取最近 eval 报告（hermetic 与 real 两轨）的失败项与成本告警，由 agent 针对指定
  用户级 skill 生成改进提案（规则条目 + 依据），人工确认后经既有 SkillEvolutionStore 落地
  （快照/版本/审计/回滚全部复用），提案记录连同前后 eval 报告摘要一起留档。
- **目标用户**：维护用户级 skill 的本仓开发者；想让 skill 随 eval 证据演进而不是凭感觉改的人。
- **核心价值**：Harness² 思路的本地化——不动模型，用评估反馈迭代外围配置；本轮只做
  「提案 + 人审」，不做自动循环。

## 2. 假设与待确认

### 2.1 已确认事实
- skill/evolve.py 已有完整存储：用户级 skill 版本快照（v<N>.md，上限 10）、`## Evolved Rules`
  追加、回滚、audit.jsonl（op=save/rollback 各一行）；只操作 ~/.nexuscli/skills/<name>/SKILL.md
- entrypoints/commands/skill.py 的 `/skill evolve <name> <rule...>` 是纯手动追加，
  `/skill rollback`、`/skill stats` 已在；repl.py SLASH_COMMANDS 已含 /skill
- eval 报告：hermetic 写 .nexuscli/evals/last-report.json（含 comparison 分档/成本 diff），
  real 轨道写 last-real-report.json（pass_k/cost/judge 明细）
- **关键边界**：hermetic eval 运行时 `config.features.skill = False`（runner.py:353），
  skill 注入被关闭以保证确定性——因此「改 skill → hermetic 分数变化」的因果链不存在。
  eval 报告在本包中是**提案依据**（失败项/成本告警指出 agent 行为缺口），不是落地效果的
  验证器；真模型轨道是否开 skill 注入未查，不在本轮依赖
- save_skill 工具已走 HITL 强制确认（requires_approval）；REPL 内有审批回调

### 2.2 关键假设
- 提案生成用一次真实 LLM 调用（走当前 provider/model），输入=目标 skill 全文 + eval 报告
  摘要（失败原因、成本告警），输出=结构化提案（若干规则条目 + 每条引用的报告证据）；
  无 Key 时明确报错不静默
- 人审形态：REPL 内逐条 y/n/s 确认（复用 approval 的提示与原因行规范），`-p` 非交互
  路径按失败关闭（不落地、提案落档）
- 提案记录落 ~/.nexuscli/skill_history/<name>/proposals/<ts>.json，含输入摘要、提案、
  人审结论、落地版本号；不改 audit.jsonl 既有语义（落地仍走 save_versioned 产生一行 op=save）

### 2.3 待确认问题
- [x] 落地效果验证怎么做？→ 结论：本轮不做机器验证（hermetic 关 skill、real 轨道不在
  依赖）；提案记录保存落地前的 eval 报告摘要供人工对照，「落地后自动再跑 eval 并 diff」
  明确挂账

### 2.4 可选解释与取舍
- 当前选择：提案与人审做成 `/skill propose <name> [--report hermetic|real]` 新子命令，
  不改既有 `/skill evolve` 语义 -> 理由：手动追加仍有价值，两条路径并存不打架
- 当前选择：只支持用户级 skill -> 理由：与 evolve.py 边界一致，builtin/project skill 的
  写入治理是另一个问题

### 2.5 已证伪路径
- 无（暂无证伪记录）

## 3. 功能范围

### 3.1 核心功能（MVP）
- [x] 提案生成：`/skill propose <name>` 读取 last-report.json（默认）或 last-real-report.json
  的失败项/成本告警 + 目标 skill 全文，经 LLM 产出结构化提案（规则条目 + 证据引用），
  无报告/无失败项/无 Key 三种空转情形都明确提示
- [x] 人审落地：逐条确认（原因行 = 该条引用的证据），确认项经 SkillEvolutionStore.save_versioned
  落地（reason="propose"），提案记录落档；`-p` 路径失败关闭
- [x] `/skill proposals <name>` 列出历史提案记录（时间、条数、落地结果）

### 3.2 扩展功能
- 无

### 3.3 不在范围内
- 自动循环、无监督批量改写、落地后自动再跑 eval 并 diff（挂账）
- builtin/project skill 的进化；prompt 片段进化
- 修改 agent 核心、eval 模块（只读消费报告）

## 4. 最小实现路径
- 最简单可行方案：新建 skill/propose.py（报告读取 + 摘要 + LLM 提案 + 记录落档）；
  commands/skill.py 加 propose/proposals 两个子命令；人审复用 approval 提示风格
- 暂不引入：新依赖、提案模板配置化、多轮对话式精炼
- 不做的抽象/配置化：不做「进化策略」接口；不做 skill 之外的进化对象

## 5. 技术决策
- 技术栈：Python 3.11+ / uv / pytest，零新依赖
- 证据协议：evidence: runs（机器任务 verify 命令由 scripts/spec_verify.py run 真实执行
  并记录 runs.jsonl；无命令任务写 `verify: manual: <理由>`；verify 命令不得自引用本包
  check 门禁）
- 本轮允许改动：src/nexuscli/skill/**、src/nexuscli/entrypoints/commands/skill.py、
  repl.py 的 /skill 分发与帮助文案、tests 新增、docs/skills.md、docs/repl-commands.md、
  README Skill 行
- 本轮不应触碰：src/nexuscli/eval/**（只读报告文件）、agent 核心、tools/builtins.py 的
  save_skill、telemetry
- Git integration branch：`spec/2026-10-27_add-skill-evolve-proposal`

### 5.1 验证策略
- 范围级别：package
- 变更对象：skill 模块（propose）、/skill 命令、skill 测试、两篇 docs
- 快速检查：`uv run --extra dev python -m pytest -q -k skill`
- 集成检查：`uv run --extra dev python -m pytest -q`（/skill 分发在 repl.py，全量兜底）
- 全项目检查：适用外：本轮不涉及共享基础设施、跨模块契约或发布载荷（skill 单模块
  + 斜杠子命令）；closeout 由发布门禁统一执行
- 升级触发：若实现发现必须改 eval 报告格式或 agent 核心，停下升级范围并记录理由

### 5.4 编排策略
- route: local
- 依据：route_decision.py = package-structure，score 0，无强结构信号一律 local
- 执行说明：实现段由主会话委托动态工作流的子代理完成（用户显式要求），主会话保留
  路由、验收、done/push 门禁

## 6. 成功标准与验证方式
- [x] 提案生成对三种空转情形明确提示、对有失败项的报告产出带证据引用的结构化提案
  （假 LLM 注入测试） -> verify: task_0001
- [x] 人审落地走 SkillEvolutionStore 产生版本快照与审计行，提案记录落档，`-p` 路径失败
  关闭不落地 -> verify: task_0001
- [x] 全量测试、Ruff 双检全绿，docs/skills.md、docs/repl-commands.md、README 与实现
  同步 -> verify: task_0002

## 7. 风险与约束
- 风险点：LLM 提案质量不稳 -> 缓解：人审是硬门，提案结构化强制每条带证据引用，无证据
  条目直接丢弃
- 风险点：误把 eval 报告当落地效果验证器 -> 缓解：spec 与文档明示边界（hermetic 关
  skill 注入），提案记录只存「落地前」报告摘要供人工对照
