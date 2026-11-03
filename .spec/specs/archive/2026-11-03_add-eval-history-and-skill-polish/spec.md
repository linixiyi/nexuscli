# eval 报告历史趋势（轻量）+ rule-demo 演示技能收窄 - 项目范围

<!-- spec-format: 2 -->

## 1. 问题定义
- **项目目标**：①eval 报告历史留痕——每次 `eval run` 后把报告追加到 `.nexuscli/evals/history/`
  （时间戳文件名），新增 `eval history [N]` 子命令列出最近 N 次运行的摘要（时间/通过数/
  全量数/cost 告警数），hermetic 与 real 轨通用；②修复包 A 遗留 UX 缺陷——rule-demo 演示
  技能的 description 过宽，日常提问被 Top-K 误匹配后模型输出 SKILL-RULE-OK 标记污染回答
  （2026-11-03 自测实证），收窄其 description 为「仅该评测场景相关」。
- **目标用户**：nightly 运营者（趋势）、日常使用者（不再被演示技能干扰）。
- **核心价值**：报告从「只有上次」变成「可回看序列」；评测 fixture 不再泄漏进日常 UX。

## 2. 假设与待确认

### 2.1 已确认事实
- reporter.write_report 每次覆写 last-report.json；real.write_real_comparison 同理；
  无任何历史留痕（2026-11-02 审计 gap ⑤）
- rule-demo 位于 .agents/skills/rule-demo/SKILL.md，description 含 "reply-format rule"，
  Top-K 匹配命中后模型遵循其标记规则污染日常回答（自测两次：命中时回答带 SKILL-RULE-OK，
  未命中时不带）
- 场景 03 的 prompt 显式点名 rule-demo（显式名 +10000 加成）——description 收窄不影响
  场景 03 的候选命中（有测试兜底）
- eval 命令组在 entrypoints/cli.py；hermetic 报告 reporter.py、real 报告 real.py

### 2.2 关键假设
- history 目录写失败（磁盘满等）不应阻断主流程——降级为一次性警告，主报告照常
- `eval history` 读 history 目录文件名排序即可，无需索引文件

### 2.3 待确认问题
- [x] history 留痕是否含 real 对比文件？→ 结论：本轮只留 hermetic/real 主报告（两轨各一
  份序列）；对比文件留痕挂账

### 2.4 可选解释与取舍
- 当前选择：history 按轨道分子目录（history/hermetic/、history/real/）-> 理由：同名
  时间戳不冲突，清理语义清晰
- 当前选择：rule-demo 收窄用 description 改写而非挪目录 -> 理由：场景 03 依赖其路径，
  挪目录是连带改动

### 2.5 已证伪路径
- 无（暂无证伪记录）

## 3. 功能范围

### 3.1 核心功能（MVP）
- [x] history 留痕：hermetic/real 两轨 run 成功后追加时间戳快照到各自子目录；写失败降级
  一次性警告；`eval history [N]` 列出该轨最近 N 次（时间、passed/total、exit 摘要、
  cost 告警计数），默认 N=10
- [x] rule-demo description 收窄为场景专属表述 + 候选匹配回归测试（一般技术查询不命中、
  显式点名命中）

### 3.2 扩展功能
- 无

### 3.3 不在范围内
- 趋势图表/跨 run diff 视图（history 先解决「有没有」，对比已有上次 diff 机制）
- 对比文件（comparison）留痕；history 清理策略

## 4. 最小实现路径
- 最简单可行方案：reporter/real 各加一个 history 追加函数（写前 mkdir exist_ok），CLI 加
  history 子命令（纯读）；rule-demo description 一行改写
- 暂不引入：历史压缩、轮转上限、趋势计算
- 不做的抽象/配置化：不做通用快照框架

## 5. 技术决策
- 技术栈：Python 3.11+ / uv / pytest，零新依赖
- 证据协议：evidence: runs（机器任务 verify 命令由 scripts/spec_verify.py run 真实执行
  并记录 runs.jsonl；无命令任务写 `verify: manual: <理由>`；verify 命令不得自引用本包
  check 门禁）
- 本轮允许改动：src/nexuscli/eval/reporter.py、src/nexuscli/eval/real.py（history 追加）、
  src/nexuscli/entrypoints/cli.py（history 子命令）、.agents/skills/rule-demo/SKILL.md、
  tests 新增、docs/eval.md、docs/repl-commands.md、README 评测回归行
- 本轮不应触碰：last-report/last-real-report/comparison 三文件既有路径与 schema、场景
  文件、场景 03 的 prompt 与断言、skill 加载链
- Git integration branch：`spec/2026-11-03_add-eval-history-and-skill-polish`

### 5.1 验证策略
- 范围级别：package
- 变更对象：eval 两轨 history 追加、CLI 子命令、rule-demo frontmatter、测试、两篇 docs
- 快速检查：`uv run --extra dev python -m pytest -q -k "eval or skill"`
- 集成检查：`uv run --extra dev python -m pytest -q`
- 全项目检查：适用外：本轮不涉及共享基础设施、跨模块契约或发布载荷；closeout 由发布
  门禁统一执行
- 升级触发：若 history 追加必须改 run 主流程结构，停下升级并记录理由

### 5.4 编排策略
- route: local
- 依据：route_decision.py = package-structure，score 0
- 执行说明：实现由主会话委托动态工作流子代理（用户显式要求）；主会话保留路由、验收、
  done/push 门禁

## 6. 成功标准与验证方式
- [x] 两轨 history 追加 + 写失败降级 + `eval history` 列示有测试 -> verify: task_0001
- [x] rule-demo 收窄后一般查询不命中、显式点名命中（场景 03 链路测试不回归）-> verify:
  task_0002
- [x] docs 两篇 + README 行同步，全量与 Ruff 双检全绿 -> verify: task_0003

## 7. 风险与约束
- 风险点：history 写入拖慢主流程 -> 缓解：单文件小写入 + 失败降级
- 风险点：description 收窄过头导致场景 03 候选不中 -> 缓解：显式名加成兜底 + 链路测试
