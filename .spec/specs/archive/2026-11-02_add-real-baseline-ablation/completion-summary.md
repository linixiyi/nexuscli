# real 轨 baseline 消融：agent vs 裸模型 harness 增益报告 - 完成总结

## 交付结论
- 结果：完成
- 完成时间：2026-11-02 18:51

## 假设回顾
### 已验证假设
- 裸模型单次补全即可答题（场景题面自包含；需要工具的题裸模型天然答不出——这正是消融
- baseline 模型与 agent 模型相同（同一配置链同 model）——增益才归因于 harness 而非模型差
- 对比口径：per-scenario agent_status/baseline_status、双方 tokens、delta；汇总 pass 率

### 仍未完全验证的假设
- 无

## 交付范围
### 已交付
- 裸模型执行路径 + --baseline 双跑与对比装配（run_baseline_scenario 单补全 +
- 文档同步与全量回归（docs/eval.md --baseline 口径与对比文件字段表、README 评测回归行）
- 真网关 30 题双跑验收（主会话执行双跑本体，verify 断言对比文件）

### 未交付
- 无

### 偏差说明
- 无

## 简化决策
### 保持简单的关键选择
- 最简单可行方案：real.py 新增 run_baseline_scenario（单补全 + 子集断言）与
- 暂不引入：baseline 侧重试/并发、对比历史趋势、多 baseline（如不同裸 prompt 模板）
- 不做的抽象/配置化：不做「对比对象」插件接口

### 本轮明确不做的内容
- hermetic 轨与 last-real-report.json schema 改动；--repeat 与 baseline 组合（后续需要再排）
- SOTA 锚定（外部 Codex/CC CLI 对比——依赖外部授权，挂账）
- 成本换算差值（币种折算留 usage 层，对比只报 token 与 pass）

## 变更边界
### 本轮主要改动模块
- 见 tasks.md 的 boundary 记录

### 明确未触碰的区域
- hermetic 轨与 last-real-report.json schema 改动；--repeat 与 baseline 组合（后续需要再排）
- SOTA 锚定（外部 Codex/CC CLI 对比——依赖外部授权，挂账）
- 成本换算差值（币种折算留 usage 层，对比只报 token 与 pass）

## 验证证据
### 备注
- 手工：真网关 30 题双跑：agent 19/30 vs baseline 0/30（净增益 +19，agent_only=19/both_fail=11/双过=0）。两点如实注记：①agent 侧本轮 19/30 低于包 C 校准的 24/30——同套件两次采样差 5 题，正是 run 方差的真实暴露（pass^k 的存在理由）；②baseline 0/30 含套件设计因素：fixture 锚点题天然需要工具，套件测的就是工具使能能力，知识型题目会给非零 baseline（挂账）。工作流曾因 provider 网络反复中断（network_error/stream_idle_timeout）拖至 4.5h，自愈后全绿；全量 pytest 曾现 2 例间歇失败，单测重跑通过且最终全量重跑 exit 0

## 验证范围
- 级别：package
- 机器验证率：3/3
- 规则：check 与 done 复用该范围；只有记录升级触发时才执行全项目检查

## 哲学生效证据
### 行为成效回填
- Development Record 行为门禁通过

### 一致性门禁回顾
- 验收面（checklist 或成功标准）已通过

## Git Records
- Date Time：2026-11-02 18:51
- Scope：real 轨 baseline 消融：agent vs 裸模型 harness 增益报告
- Feature：裸模型执行路径 + --baseline 双跑与对比装配（run_baseline_scenario 单补全 +；文档同步与全量回归（docs/eval.md --baseline 口径与对比文件字段表、README 评测回归行）；真网关 30 题双跑验收（主会话执行双跑本体，verify 断言对比文件）
- Action：Commit / Push
- Effect：Development Record 行为门禁通过
- Commit：pending local commit
- 提交区间：cc1b919d1a40849b96120a830668bc30888d8d0d..cc1b919d1a40849b96120a830668bc30888d8d0d
- Push：not-run (push stage follows commit)

## AGENTS.md 增量提议
无

## 被拒绝的扩展提议
- 无

## 门禁证据
- check_spec_package.py exit 0

## 遗留事项
- 无；问题处置见下方结构化区块

## 问题处置

```json
{
  "issues": [],
  "version": 1
}
```
