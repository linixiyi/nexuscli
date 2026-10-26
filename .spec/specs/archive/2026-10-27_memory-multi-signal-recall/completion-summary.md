# Mem0 式多信号记忆召回 - 完成总结

## 交付结论
- 结果：完成
- 完成时间：2026-10-26 17:18

## 假设回顾
### 已验证假设
- BM25 对小 batch（scope 内记忆条数 ≤1000）内存现算的性能可接受（无索引）
- 实体抽取不引 NLP 依赖：save_memory 工具加可选 entities 参数（LLM 供给）+
- 查询意图（时态加权）用中英文关键词表判别即可，不需要 LLM 参与

### 仍未完全验证的假设
- 无

## 交付范围
### 已交付
- task_0001 信号层与融合（MemorySignal 接口 + 三路信号 + 时间分层 + schema 迁移）
- task_0002 工具面与注入面（save_memory 扩参 + 时态注入展示）
- task_0003 评测场景与对抗验证
- task_0004 文档联动
- task_0005 全量门与真实入口冒烟

### 未交付
- 无

### 偏差说明
- 无

## 简化决策
### 保持简单的关键选择
- 最简单可行方案：signals.py（MemorySignal 协议 + lexical/bm25/entity/temporal 四个
- 暂不引入：向量/embedding、LLM 实体抽取、查询改写、按信号分块展示、跨 scope 召回
- 不做的抽象/配置化：权重不做 config 化（代码内常量+单测锚定）、不做信号注册表
- 合并顺序约束：本分支引入 OpenViking AGPL 代码，必须后于 2026-10-27_agpl-relicense

### 本轮明确不做的内容
- 不引入 mem0 依赖、不引入向量库/embedding 依赖
- /goal 并入 memory 存储（保持独立 JSON，仅时态加权时把 goal 视为 current_state
- subagent 文件制记忆（agent-memory/）改造
- prompt 自动召回的分块展示重构（assembler 只做最小适配）

## 变更边界
### 本轮主要改动模块
- 见 tasks.md 的 boundary 记录

### 明确未触碰的区域
- 不引入 mem0 依赖、不引入向量库/embedding 依赖
- /goal 并入 memory 存储（保持独立 JSON，仅时态加权时把 goal 视为 current_state
- subagent 文件制记忆（agent-memory/）改造
- prompt 自动召回的分块展示重构（assembler 只做最小适配）

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
- 日期时间：2026-10-26 17:18
- 范围：Mem0 式多信号记忆召回
- 功能：task_0001 信号层与融合（MemorySignal 接口 + 三路信号 + 时间分层 + schema 迁移）；task_0002 工具面与注入面（save_memory 扩参 + 时态注入展示）；task_0003 评测场景与对抗验证；task_0004 文档联动；task_0005 全量门与真实入口冒烟
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
