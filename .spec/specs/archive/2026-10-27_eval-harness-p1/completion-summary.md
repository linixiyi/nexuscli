# 离线 eval harness 三项增强 - 完成总结

## 交付结论
- 结果：完成
- 完成时间：2026-10-26 12:39

## 假设回顾
### 已验证假设
- 三种新断言可在现有 _assertion_failures 的判定结构内表达，不需要改 runner 主循环
- difficulty 三档（short/medium/long）足够承载「退化集中在哪一档」的定位需求

### 仍未完全验证的假设
- 无

## 交付范围
### 已交付
- task_0001 轨迹断言增强（required_tools / max_tool_calls / max_total_tool_calls）
- task_0002 场景难度分档（difficulty 字段 + 按档聚合）
- task_0003 成本确定性指标（turns / total_tool_calls 进报告 + diff 告警）
- task_0004 文档同步（docs/eval.md + README 行）
- task_0005 全量门与真实入口

### 未交付
- 无

### 偏差说明
- 无

## 简化决策
### 保持简单的关键选择
- 最简单可行方案：三种断言进现有 EvalAssertions/_parse_assertions/_assertion_failures
- 暂不引入：断言 DSL/表达式语言、按工具参数维度的断言、分档权重、成本预算门槛
- 不做的抽象/配置化：不把分档做成可配置枚举（固定三档）、不做自定义报告模板
- 全部保持 hermetic：脚本化假 LLM、stub 工具、无网络、无真实 token

### 本轮明确不做的内容
- 用户模拟场景、记忆召回评测、真模型轨道、许可变更（挂账后续包）
- 不改 runner 主循环结构与 agent 本体；不动 docs/eval.md 之外的文档（README 特性表

## 变更边界
### 本轮主要改动模块
- 见 tasks.md 的 boundary 记录

### 明确未触碰的区域
- 用户模拟场景、记忆召回评测、真模型轨道、许可变更（挂账后续包）
- 不改 runner 主循环结构与 agent 本体；不动 docs/eval.md 之外的文档（README 特性表

## 验证证据
### 备注
- 机器证据：runs.jsonl（见验证范围的机器验证率）

## 验证范围
- 级别：package
- 机器验证率：5/5
- 规则：check 与 done 复用该范围；只有记录升级触发时才执行全项目检查

## 哲学生效证据
### 行为成效回填
- Development Record 行为门禁通过

### 一致性门禁回顾
- 验收面（checklist 或成功标准）已通过

## Git 记录
- 日期时间：2026-10-26 12:39
- 范围：离线 eval harness 三项增强
- 功能：task_0001 轨迹断言增强（required_tools / max_tool_calls / max_total_tool_calls）；task_0002 场景难度分档（difficulty 字段 + 按档聚合）；task_0003 成本确定性指标（turns / total_tool_calls 进报告 + diff 告警）；task_0004 文档同步（docs/eval.md + README 行）；task_0005 全量门与真实入口
- 操作：提交 / 推送
- 成效：Development Record 行为门禁通过
- 提交：pending local commit
- 提交区间：9dc1243cb524f26ac836c2280de6d2fb554a8986..9dc1243cb524f26ac836c2280de6d2fb554a8986
- 推送：not-run (push stage follows commit)

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
