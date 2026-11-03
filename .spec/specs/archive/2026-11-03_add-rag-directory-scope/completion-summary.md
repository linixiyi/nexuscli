# rag 目录限定检索：OpenViking expr.py 搬运 - 完成总结

## 交付结论
- 结果：完成
- 完成时间：2026-11-03 13:00

## 假设回顾
### 已验证假设
- expr.py 是过滤表达式解析/求值（路径模式匹配为核心语义），可在不引入上游两条依赖轴的
- `search` 加可选 `scope` 参数（子树相对路径），默认 None = 现行为逐字节兼容

### 仍未完全验证的假设
- 无

## 交付范围
### 已交付
- expr 搬运 + scope 检索（clone 上游 @ 9a57854 只读参考；rag 新增 expr/路径限定模块
- 文档同步与全量回归（docs/tools.md 检索节补 scope 用法；README 检索相关行核对）

### 未交付
- 无

### 偏差说明
- 无

## 简化决策
### 保持简单的关键选择
- 最简单可行方案：rag 新增 expr 模块（搬运）+ code_index 搜索结果路径过滤 + builtins
- 暂不引入：scope 的 glob/regex 语法、多 scope 并集
- 不做的抽象/配置化：不做通用过滤表达式框架

### 本轮明确不做的内容
- 复杂布尔过滤语法面（只取路径限定）、memory 侧复用、UI 暴露

## 变更边界
### 本轮主要改动模块
- 见 tasks.md 的 boundary 记录

### 明确未触碰的区域
- 复杂布尔过滤语法面（只取路径限定）、memory 侧复用、UI 暴露

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

## Git Records
- Date Time：2026-11-03 13:00
- Scope：rag 目录限定检索：OpenViking expr.py 搬运
- Feature：expr 搬运 + scope 检索（clone 上游 @ 9a57854 只读参考；rag 新增 expr/路径限定模块；文档同步与全量回归（docs/tools.md 检索节补 scope 用法；README 检索相关行核对）
- Action：Commit / Push
- Effect：Development Record 行为门禁通过
- Commit：pending local commit
- 提交区间：ccfd8db2184aee6c115ebdd207c39765a1d4e047..ccfd8db2184aee6c115ebdd207c39765a1d4e047
- Push：not-run (push stage follows commit)

## AGENTS.md 增量提议
```diff
OpenViking【直接可用】件的搬运先读其求值契约再决定整搬/改写：expr.py 本体是 9 个无人消费的过滤 AST 节点，真正语义在 vectordb 适配器的 PathScope 求值里——逐字整搬会引入死代码与依赖轴，取目标行为（前缀子树匹配 + 转义 + 归一）语义改写更干净（来源：2026-11-03_add-rag-directory-scope 搬运方式结论）
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
