# 套件稳定性基线：repeat=3 pass^k 全量真网关测定 - 项目范围

<!-- spec-format: 2 -->

## 1. 问题定义
- **项目目标**：对 30 题套件跑 `--repeat 3`（90 次真网关执行），得到 pass^3 稳定性基线
  （全通过率 + 逐题通过次数分布），回填 manifest 的 stability 字段，并在 docs 量化方差
  ——终结「单次采样 24/30 vs 19/30 摆动 ±5 无口径」的状态。
- **目标用户**：nightly 运营者与任何引用套件数字的人。
- **核心价值**：套件的发布口径从「单次采样」升级为「稳定性口径」（pass^3），方差量级
  有了测定值。

## 2. 假设与待确认

### 2.1 已确认事实
- 两次单次采样：校准 24/30（80%）、消融 agent 侧 19/30（63.3%）——摆动 ±5 题无口径
- pass^k 聚合已在包 5 落地（pass_k 块：repeat/passed_runs/all_pass + summary.all_pass_rate）
- `--repeat 3` 与 `--file` 目录可组合（包 D 只禁了 repeat×baseline 组合）
- 单次全量约 50 分钟 → repeat=3 约 2.5 小时、约 150-200 万 tokens（本地网关，一次性
  测定成本）
- 深夜网关/网络稳定性是已知风险（今晚 provider 多次中断）——中途崩溃则重跑

### 2.2 关键假设
- 90 次执行的 run 方差本身是交付物的一部分（逐题通过次数分布会暴露「稳定过/稳定败/
  摇摆题」三分类——摇摆题正是 pass^k 想抓的）
- stability 回填不动 calibration 字段（单次口径保留历史），新字段独立

### 2.3 待确认问题
- [x] repeat=3 若因网关故障中途崩怎么办？→ 结论：重跑一次；二次崩溃改 repeat=2 并在
  docs 注明降级原因

### 2.4 可选解释与取舍
- 当前选择：k=3 -> 理由：k=2 区分不了「2/2 稳定」与运气；3 是成本与置信的平衡点
- 当前选择：stability 回填 manifest 而非独立文件 -> 理由：单题档案一处齐全

### 2.5 已证伪路径
- 无（暂无证伪记录）

## 3. 功能范围

### 3.1 核心功能（MVP）
- [x] repeat=3 全量真网关测定：last-real-report.json 含 30 题 × pass_k.repeat=3
- [x] manifest 逐题回填 stability（repeat/passed_runs/all_pass）+ docs/eval.md 方差量化段
  （三次采样对比：校准 24/30、消融 19/30、pass^3 = N/30，摇摆题清单）

### 3.2 扩展功能
- 无

### 3.3 不在范围内
- k>3、跨模型稳定性、nightly 调度接线（cron 挂载待用户定节奏）

## 4. 最小实现路径
- 最简单可行方案：一次后台长跑 + 回填脚本 + docs 一段；零 src 改动
- 暂不引入：稳定性趋势追踪、告警阈值
- 不做的抽象/配置化：无

## 5. 技术决策
- 技术栈：零依赖零 src 改动
- 证据协议：evidence: runs（机器任务 verify 命令由 scripts/spec_verify.py run 真实执行
  并记录 runs.jsonl；无命令任务写 `verify: manual: <理由>`；verify 命令不得自引用本包
  check 门禁）
- 本轮允许改动：examples/evals/suite/meta/manifest.json（stability 字段）、docs/eval.md、
  README 评测回归行、包内 evidence
- 本轮不应触碰：场景题面与断言、src/**、calibration 字段
- Git integration branch：`spec/2026-11-02_add-suite-stability-baseline`

### 5.1 验证策略
- 范围级别：package
- 变更对象：manifest、docs/eval.md、README
- 快速检查：`uv run --extra dev python -m pytest -q tests/test_eval_suite.py`
- 集成检查：`uv run --extra dev python -m pytest -q`
- 全项目检查：适用外：纯数据集测定包；closeout 由发布门禁统一执行
- 升级触发：若发现必须改 src 才能承载回填，停下升级并记录理由

### 5.4 编排策略
- route: local
- 依据：route_decision.py = package-structure，score 0
- 执行说明：repeat=3 长跑由主会话后台执行（AGENTS.md「执行环境与冒烟」守则 +
  spec_verify 600s 上限，同包 C/D 处置）；回填与文档由子代理完成

## 6. 成功标准与验证方式
- [x] last-real-report.json 30 题 pass_k.repeat=3 且 summary.all_pass_rate 有值 ->
  verify: task_0001
- [x] manifest 逐题 stability 回填、docs 方差段（三次采样对比 + 摇摆题清单）、全量与
  Ruff 双检全绿 -> verify: task_0002

## 7. 风险与约束
- 风险点：网关中途故障导致长跑报废 -> 缓解：重跑一次；再崩降级 repeat=2 注明
- 风险点：约 200 万 tokens 消耗 -> 缓解：一次性测定，落档后无需重复
