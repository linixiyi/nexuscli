# provider 细节与 mypy 纪律（F12/F13） - 完成总结

## 交付结论
- 结果：完成
- 完成时间：2026-10-25 15:25

## 假设回顾
### 已验证假设
- F12 的修改半径在 src/nexuscli/llm/anthropic.py + agent.py 的截断处理（盘点表定位）
- F13 排除清单以摸底实测为准；新改动模块必须通过

### 仍未完全验证的假设
- 无

## 交付范围
### 已交付
- F12 Anthropic provider 细节对齐
- F13 mypy 渐进接入 CI
- 全量回归（CI 三步）
- 计划同步：勾选 master F12/F13 + .spec/autorun/ 摘要

### 未交付
- 无

### 偏差说明
- 无

## 简化决策
### 保持简单的关键选择
- 最简单可行方案：F12 按 05 盘点表逐项补齐并补核实测试；F13 mypy.ini/pyproject 配置 + 排除清单 + CI job
- 暂不引入：mypy 插件、strict 全仓、provider 重试的通用抽象
- 不做的抽象/配置化：退避参数写常量（1/2/4s 对标 kitagent）

### 本轮明确不做的内容
- 全仓 mypy 清零承诺；OpenAI-compatible 路径的同款重试（挂账）；llm/ 重写

## 变更边界
### 本轮主要改动模块
- 见 tasks.md 的 boundary 记录

### 明确未触碰的区域
- 全仓 mypy 清零承诺；OpenAI-compatible 路径的同款重试（挂账）；llm/ 重写

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

## Git Records
- Date Time：2026-10-25 15:25
- Scope：provider 细节与 mypy 纪律（F12/F13）
- Feature：F12 Anthropic provider 细节对齐；F13 mypy 渐进接入 CI；全量回归（CI 三步）；计划同步：勾选 master F12/F13 + .spec/autorun/ 摘要
- Action：Commit / Push
- Effect：Development Record 行为门禁通过
- Commit：pending local commit
- 提交区间：711e2fad414b779fe56dd0a9328863155db76e2d..711e2fad414b779fe56dd0a9328863155db76e2d
- Push：not-run (push stage follows commit)

## AGENTS.md 增量提议
```diff
- Anthropic 原生 provider 的三处协议级细节值得保持：max_tokens 截断落在 tool_use 中间时必须合成 is_error tool_result（否则 assistant tool_use 无配对 result 会被 API 拒绝）；流断线重试只认传输层错误且重试期间不重复发布 token 事件；prompt caching 打在 system 块与最后一个工具上（前缀匹配原理）。改 provider 时先跑 tests/test_anthropic_client.py。
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
