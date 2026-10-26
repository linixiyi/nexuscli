# eval 新场景类型：多轮用户模拟与记忆召回评测 - 完成总结

## 交付结论
- 结果：完成
- 完成时间：2026-10-27 04:20

## 假设回顾
### 已验证假设
- 多轮用户模拟可在 eval runner 层实现：agent 产出最终文本后由 runner 注入下一条用户
- 记忆召回评测不走 agent loop：场景直接构造 MemoryManager（临时 DB）+ 断言 recall
- schema 新键全部可选，现有内置场景（01-03）零改动可跑

### 仍未完全验证的假设
- 无

## 交付范围
### 已交付
- 多轮用户模拟场景类型（schema + runner 多轮驱动 + 跨轮断言 + ≥2 内置示例 + 测试）
- 记忆召回评测场景类型（seed + recall 排序断言 + ≥2 内置示例 + 测试）
- 文档同步与全量回归
- 真实入口验收

### 未交付
- 无

### 偏差说明
- 无

## 简化决策
### 保持简单的关键选择
- 最简单可行方案：多轮逻辑全部收在 eval/runner.py 的场景执行层（轮循环 + 同会话注入
- 暂不引入：真实 LLM、embedding、任何新依赖
- 不做的抽象/配置化：不为「用户人格」建模（用户脚本就是数据）；不把多轮逻辑泛化成

### 本轮明确不做的内容
- 真模型 eval 轨道（pass^k / 真实成本 / LLM-as-judge）——后续包
- OTLP 导出、skill 自进化——后续包
- src/nexuscli/agent/ 与 memory 语义的任何改动

## 变更边界
### 本轮主要改动模块
- 见 tasks.md 的 boundary 记录

### 明确未触碰的区域
- 真模型 eval 轨道（pass^k / 真实成本 / LLM-as-judge）——后续包
- OTLP 导出、skill 自进化——后续包
- src/nexuscli/agent/ 与 memory 语义的任何改动

## 验证证据
### 备注
- 机器证据：runs.jsonl（见验证范围的机器验证率）

## 验证范围
- 级别：package
- 机器验证率：4/4
- 规则：check 与 done 复用该范围；只有记录升级触发时才执行全项目检查

## 哲学生效证据
### 行为成效回填
- Development Record 行为门禁通过

### 一致性门禁回顾
- 验收面（checklist 或成功标准）已通过

## Git 记录
- 日期时间：2026-10-27 04:20
- 范围：eval 新场景类型：多轮用户模拟与记忆召回评测
- 功能：多轮用户模拟场景类型（schema + runner 多轮驱动 + 跨轮断言 + ≥2 内置示例 + 测试）；记忆召回评测场景类型（seed + recall 排序断言 + ≥2 内置示例 + 测试）；文档同步与全量回归；真实入口验收
- 操作：提交 / 推送
- 成效：Development Record 行为门禁通过
- 提交：pending local commit
- 提交区间：11cd0ae33163edfdf0b30d6608fc2dbebb9f6a0b..11cd0ae33163edfdf0b30d6608fc2dbebb9f6a0b
- 推送：not-run (push stage follows commit)

## AGENTS.md 增量提议
```diff
eval 场景需要临时文件时必须用 tempfile 而不是 pytest tmp_path：`nexuscli eval run` 是真实入口、不经 pytest，fixture 不存在；tests/conftest.py 的 HOME/USERPROFILE 重定向只护 pytest 路径（来源：2026-10-27_add-eval-scenario-types 记忆评测场景实现）
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
