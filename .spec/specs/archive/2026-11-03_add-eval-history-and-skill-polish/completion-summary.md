# eval 报告历史趋势（轻量）+ rule-demo 演示技能收窄 - 完成总结

## 交付结论
- 结果：完成
- 完成时间：2026-11-03 11:20

## 假设回顾
### 已验证假设
- history 目录写失败（磁盘满等）不应阻断主流程——降级为一次性警告，主报告照常
- `eval history` 读 history 目录文件名排序即可，无需索引文件

### 仍未完全验证的假设
- 无

## 交付范围
### 已交付
- rule-demo description 收窄 + 候选匹配回归测试（一般技术查询不命中、显式点名命中；
- eval history：两轨 run 成功后追加时间戳快照到 .nexuscli/evals/history/{hermetic,real}/
- 文档同步与全量回归（docs/eval.md history 小节、docs/repl-commands.md 子命令表、

### 未交付
- 无

### 偏差说明
- 无

## 简化决策
### 保持简单的关键选择
- 最简单可行方案：reporter/real 各加一个 history 追加函数（写前 mkdir exist_ok），CLI 加
- 暂不引入：历史压缩、轮转上限、趋势计算
- 不做的抽象/配置化：不做通用快照框架

### 本轮明确不做的内容
- 趋势图表/跨 run diff 视图（history 先解决「有没有」，对比已有上次 diff 机制）
- 对比文件（comparison）留痕；history 清理策略

## 变更边界
### 本轮主要改动模块
- 见 tasks.md 的 boundary 记录

### 明确未触碰的区域
- 趋势图表/跨 run diff 视图（history 先解决「有没有」，对比已有上次 diff 机制）
- 对比文件（comparison）留痕；history 清理策略

## 验证证据
### 备注
- 机器证据：runs.jsonl（见验证范围的机器验证率）

## 验证范围
- 级别：package
- 机器验证率：3/3
- 规则：check 与 done 复用该范围；只有记录升级触发时才执行全项目检查

## 哲学生效证据
### 行为成效回填
- Development Record 行为门禁通过

### 一致性门禁回顾
- 验收面（checklist 或成功标准）已通过

## Git 记录
- 日期时间：2026-11-03 11:20
- 范围：eval 报告历史趋势（轻量）+ rule-demo 演示技能收窄
- 功能：rule-demo description 收窄 + 候选匹配回归测试（一般技术查询不命中、显式点名命中；；eval history：两轨 run 成功后追加时间戳快照到 .nexuscli/evals/history/{hermetic,real}/；文档同步与全量回归（docs/eval.md history 小节、docs/repl-commands.md 子命令表、
- 操作：提交 / 推送
- 成效：Development Record 行为门禁通过
- 提交：pending local commit
- 提交区间：61663bac75e2201f0567ffdafb34171851c1fc5b..61663bac75e2201f0567ffdafb34171851c1fc5b
- 推送：not-run (push stage follows commit)

## AGENTS.md 增量提议
```diff
评测 fixture 型技能（演示/考题用途）的 description 必须自证「仅供评测场景」并在 tags 标注 fixture——否则 Top-K 会把格式规则类演示技能捞进日常回答，模型无条件遵循其标记规则污染输出（来源：2026-11-03_add-eval-history-and-skill-polish rule-demo 收窄，自测两次实证）
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
