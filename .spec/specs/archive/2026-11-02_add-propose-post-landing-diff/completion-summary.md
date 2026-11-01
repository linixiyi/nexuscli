# propose 落地后自动 real-eval 对比（闭环后半环） - 完成总结

## 交付结论
- 结果：完成
- 完成时间：2026-11-02 07:35

## 假设回顾
### 已验证假设
- run_real_scenarios 可注入假实现（llm_client_factory 缝已在，包 A 用过）——单测不打真网关
- 报告备份/还原用「读入内存→跑→写回原内容」即可（文件小）；还原失败必须告警且不得静默
- diff 维度最小化：目标场景 status/pass_k.all_pass/失败原因变化 + summary.all_pass_rate；

### 仍未完全验证的假设
- 无

## 交付范围
### 已交付
- 落地后 diff 四步函数 + propose 尾段接线（备份→跑→对比→还原；二次确认 Reason 行；
- 文档同步与全量回归（docs/skills.md 闭环后半环节、README Skill 行）
- 真实链路验收（主会话经 spec_verify：真网关跑一次四步函数）

### 未交付
- 无

### 偏差说明
- 无

## 简化决策
### 保持简单的关键选择
- 最简单可行方案：propose.py 加 run_post_landing_diff（备份/跑/对比/还原四步纯函数 +
- 暂不引入：diff 报告的独立文件、多场景参数化、预算熔断
- 不做的抽象/配置化：不做「效果验证策略」接口

### 本轮明确不做的内容
- 独立 /skill verify 子命令；全量 real 对比；hermetic 轨对比（因果链不存在）
- 自动循环（落地→评测→再提案的无人值守链路仍不做）

## 变更边界
### 本轮主要改动模块
- 见 tasks.md 的 boundary 记录

### 明确未触碰的区域
- 独立 /skill verify 子命令；全量 real 对比；hermetic 轨对比（因果链不存在）
- 自动循环（落地→评测→再提案的无人值守链路仍不做）

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
- 日期时间：2026-11-02 07:35
- 范围：propose 落地后自动 real-eval 对比（闭环后半环）
- 功能：落地后 diff 四步函数 + propose 尾段接线（备份→跑→对比→还原；二次确认 Reason 行；；文档同步与全量回归（docs/skills.md 闭环后半环节、README Skill 行）；真实链路验收（主会话经 spec_verify：真网关跑一次四步函数）
- 操作：提交 / 推送
- 成效：Development Record 行为门禁通过
- 提交：pending local commit
- 提交区间：45566b5bb7ce0870626be40cd6235fefcb542b51..45566b5bb7ce0870626be40cd6235fefcb542b51
- 推送：not-run (push stage follows commit)

## AGENTS.md 增量提议
```diff
main 会话执行的 verify 一行脚本两个坑： 后接 from import 是语法错误（用 import module as alias 全分号风格）；断言不变量必须取「调用前现场」而不是假设基线形态——此前任务可能已用 --file 覆写过共享报告文件，还原类验证比对的是前后一致不是某个预设值（来源：2026-11-02_add-propose-post-landing-diff task_0003 两次 verify 失败复盘）
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
