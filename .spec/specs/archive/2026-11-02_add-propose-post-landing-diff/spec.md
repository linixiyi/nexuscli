# propose 落地后自动 real-eval 对比（闭环后半环） - 项目范围

<!-- spec-format: 2 -->

## 1. 问题定义
- **项目目标**：补全 Harness² 闭环后半环——`/skill propose` 人审落地完成后，可选自动跑一次
  real eval（默认场景 03_skill_rule_follow，skill 规则遵循验证器），把「落地前报告 → 落地后
  报告」的对比写进提案记录；真实调用前必须再要一次人工确认（Reason 行说明会产生真实网关
  调用）。
- **目标用户**：维护用户级 skill 的本仓开发者；nightly 轨道运营者。
- **核心价值**：skill 改动从「凭感觉」变成「有前后对比证据」；提案记录成为完整的
  提案→人审→落地→效果链路档案。

## 2. 假设与待确认

### 2.1 已确认事实
- propose 落地点：entrypoints/commands/skill.py:292 save_versioned(reason="propose")，人审
  逐条 y/n/s（:269-305），非 TTY 失败关闭（:257 isatty 门）
- 提案记录：~/.nexuscli/skill_history/<name>/proposals/<ts>.json（含落地前报告摘要）
- real 轨可编程入口：eval/real.py run_real_scenarios；场景 03（2026-11-02_add-skill-
  layered-loading 落地）是真模型 skill 规则遵循验证器，单跑 ~12K tokens、真模型首跑 PASS
- run_real_scenarios 会覆写 .nexuscli/evals/last-real-report.json——diff 跑完必须恢复原
  文件，否则破坏 nightly 全量基线
- hermetic 轨 features.skill=False，落地效果验证只能走 real 轨（包 7 spec 边界 + 包 A 修复
  后链路可达）
- AGENTS.md「执行环境与冒烟」：真实网关调用必须主会话执行；REPL 内由用户本人在真实终端
  触发等价于主会话语境

### 2.2 关键假设
- run_real_scenarios 可注入假实现（llm_client_factory 缝已在，包 A 用过）——单测不打真网关
- 报告备份/还原用「读入内存→跑→写回原内容」即可（文件小）；还原失败必须告警且不得静默
- diff 维度最小化：目标场景 status/pass_k.all_pass/失败原因变化 + summary.all_pass_rate；
  不做逐 token 成本对比（usage 波动是噪声）

### 2.3 待确认问题
- [x] 落地后自动跑还是问一次？→ 结论：问一次（Reason 行注明真实调用与预估场景），y 才
  跑；非 TTY 直接跳过并记录 skipped_reason——与审批哲学一致，不做无人值守真实调用

### 2.4 可选解释与取舍
- 当前选择：默认只跑场景 03（--file 语义），不跑全量 -> 理由：token 成本与闭环聚焦；
  全量 diff 属 nightly 职责
- 当前选择：diff 结果只进提案记录，不回写 last-real-report.json 的 comparison 块 -> 理由：
  real 轨明确无 regression diff（real.py:23-25），提案记录是这条链路的正确归档处
- 当前选择：不做独立 /skill verify 子命令（对历史提案补跑）-> 理由：最小闭环；需要时
  再加（记录在不在范围内）

### 2.5 已证伪路径
- 无（暂无证伪记录）

## 3. 功能范围

### 3.1 核心功能（MVP）
- [x] propose 落地完成 → 提示「跑 real eval 对比？」（Reason: 真实网关调用，场景
  03_skill_rule_follow，约 1-2 万 token）→ y：备份 last-real-report.json → 跑场景 03 →
  对比（落地前摘要 vs 新报告同场景）→ 写提案记录 post_landing 字段（before/after status、
  失败原因变化、all_pass_rate）→ 还原备份；n/s：记录 declined；还原失败显式告警
- [x] 三类退化路径：非 TTY（skipped_reason=non-interactive）、无 Key（带指引）、场景执行
  异常（记录错误，不影响落地本身已完成的语义）
- [x] 测试（假 LLM + 注入假 run_real_scenarios）：确认跑/拒绝跑/非 TTY/无 Key/执行异常/
  备份还原（含还原失败告警）/diff 字段两侧

### 3.2 扩展功能
- 无

### 3.3 不在范围内
- 独立 /skill verify 子命令；全量 real 对比；hermetic 轨对比（因果链不存在）
- 自动循环（落地→评测→再提案的无人值守链路仍不做）

## 4. 最小实现路径
- 最简单可行方案：propose.py 加 run_post_landing_diff（备份/跑/对比/还原四步纯函数 +
  可注入 runner）；skill.py 人审尾段接线（确认提示 + 调用 + 记录字段）
- 暂不引入：diff 报告的独立文件、多场景参数化、预算熔断
- 不做的抽象/配置化：不做「效果验证策略」接口

## 5. 技术决策
- 技术栈：Python 3.11+ / uv / pytest，零新依赖
- 证据协议：evidence: runs（机器任务 verify 命令由 scripts/spec_verify.py run 真实执行
  并记录 runs.jsonl；无命令任务写 `verify: manual: <理由>`；verify 命令不得自引用本包
  check 门禁）
- 本轮允许改动：src/nexuscli/skill/propose.py、src/nexuscli/entrypoints/commands/skill.py、
  tests 新增、docs/skills.md、README Skill 行
- 本轮不应触碰：eval/real.py 与 reporter（只调用不修改）、evolve.py 语义、其他命令
- Git integration branch：`spec/2026-11-02_add-propose-post-landing-diff`

### 5.1 验证策略
- 范围级别：package
- 变更对象：skill 模块（propose 尾段）、/skill 命令、skill 测试、docs/skills.md
- 快速检查：`uv run --extra dev python -m pytest -q -k skill`
- 集成检查：`uv run --extra dev python -m pytest -q`（skill.py 被 repl 引用，全量兜底）
- 全项目检查：适用外：本轮不涉及共享基础设施、跨模块契约或发布载荷；closeout 由发布
  门禁统一执行
- 升级触发：若必须改 eval/real.py 才能注入或取回报告，停下升级并记录理由

### 5.4 编排策略
- route: local
- 依据：route_decision.py = package-structure，score 0
- 执行说明：task_0001/0002 由主会话委托动态工作流子代理实现（用户显式要求）；task_0003
  真实验收按 AGENTS.md「执行环境与冒烟」守则由 spec_verify 在主会话执行

## 6. 成功标准与验证方式
- [x] 确认后跑/拒绝/非 TTY/无 Key/执行异常/备份还原失败告警/diff 字段两侧，全部有测试
  -> verify: task_0001
- [x] docs/skills.md 与 README 同步，全量与 Ruff 双检全绿 -> verify: task_0002
- [x] 真实链路：python 直调四步函数（真网关跑场景 03），提案记录含 post_landing 且原
  last-real-report.json 逐字节还原 -> verify: task_0003

## 7. 风险与约束
- 风险点：报告还原失败污染 nightly 基线 -> 缓解：先内存备份再跑；还原失败显式告警 +
  提案记录标注 report_restored=false
- 风险点：REPL 内真实调用让用户意外付费 -> 缓解：二次确认 + Reason 行写明预估成本
- 风险点：模型遵循率波动让 diff 信号噪声大 -> 缓解：单次对比只作档案信号不做门禁；
  pass_k/repeat 升级留待需要时
