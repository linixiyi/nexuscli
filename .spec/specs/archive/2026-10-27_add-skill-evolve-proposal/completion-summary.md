# eval 当 probe 的 skill 自进化最小闭环（提案+人审） - 完成总结

## 交付结论
- 结果：完成
- 完成时间：2026-10-27 21:20

## 假设回顾
### 已验证假设
- 提案生成用一次真实 LLM 调用（走当前 provider/model），输入=目标 skill 全文 + eval 报告
- 人审形态：REPL 内逐条 y/n/s 确认（复用 approval 的提示与原因行规范），`-p` 非交互
- 提案记录落 ~/.nexuscli/skill_history/<name>/proposals/<ts>.json，含输入摘要、提案、

### 仍未完全验证的假设
- 无

## 交付范围
### 已交付
- 提案生成 + 人审落地 + 提案记录（skill/propose.py、/skill propose 与 /skill proposals
- 文档同步与全量回归

### 未交付
- 无

### 偏差说明
- 无

## 简化决策
### 保持简单的关键选择
- 最简单可行方案：新建 skill/propose.py（报告读取 + 摘要 + LLM 提案 + 记录落档）；
- 暂不引入：新依赖、提案模板配置化、多轮对话式精炼
- 不做的抽象/配置化：不做「进化策略」接口；不做 skill 之外的进化对象

### 本轮明确不做的内容
- 自动循环、无监督批量改写、落地后自动再跑 eval 并 diff（挂账）
- builtin/project skill 的进化；prompt 片段进化
- 修改 agent 核心、eval 模块（只读消费报告）

## 变更边界
### 本轮主要改动模块
- 见 tasks.md 的 boundary 记录

### 明确未触碰的区域
- 自动循环、无监督批量改写、落地后自动再跑 eval 并 diff（挂账）
- builtin/project skill 的进化；prompt 片段进化
- 修改 agent 核心、eval 模块（只读消费报告）

## 验证证据
### 备注
- 机器证据：runs.jsonl（见验证范围的机器验证率）

## 验证范围
- 级别：package
- 机器验证率：2/2
- 规则：check 与 done 复用该范围；只有记录升级触发时才执行全项目检查

## 哲学生效证据
### 行为成效回填
- Development Record 行为门禁通过

### 一致性门禁回顾
- 验收面（checklist 或成功标准）已通过

## Git 记录
- 日期时间：2026-10-27 21:20
- 范围：eval 当 probe 的 skill 自进化最小闭环（提案+人审）
- 功能：提案生成 + 人审落地 + 提案记录（skill/propose.py、/skill propose 与 /skill proposals；文档同步与全量回归
- 操作：提交 / 推送
- 成效：Development Record 行为门禁通过
- 提交：pending local commit
- 提交区间：e57a01d9a6d313beea223a9f388c320482cf8aaf..e57a01d9a6d313beea223a9f388c320482cf8aaf
- 推送：not-run (push stage follows commit)

## AGENTS.md 增量提议
```diff
本仓 -p 模式不分发斜杠命令（cli.py _run_prompt 直通引擎），交互/非交互分支要落在功能自身（如 sys.stdin.isatty() 门，approval.py 同款）而不是斜杠分发层——依赖「-p 不执行斜杠命令」做失败关闭会漏掉管道等非 TTY 场景（来源：2026-10-27_add-skill-evolve-proposal propose 落地门实现）
```

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
