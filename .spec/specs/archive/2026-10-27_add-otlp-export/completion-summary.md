# 可选 OTLP 导出端点（Langfuse 等外挂观测） - 完成总结

## 交付结论
- 结果：完成
- 完成时间：2026-10-27 12:29

## 假设回顾
### 已验证假设
- exporter 缺包走「四步套路」：新 extras 组（或并入语义贴合的既有组）+ 惰性导入 +
- 导出是 fire-and-forget 旁路：BatchSpanProcessor 的失败只产生一次性警告，不阻塞命令
- 本地假 OTLP HTTP 接收端（测试内起线程 http.server）足以验证导出与降级两条路径

### 仍未完全验证的假设
- 无

## 交付范围
### 已交付
- OTLP 导出与降级路径（telemetry.otlp 配置三键、exporter 惰性导入进 extras、
- 文档同步与全量回归

### 未交付
- 无

### 偏差说明
- 无

## 简化决策
### 保持简单的关键选择
- 最简单可行方案：tracing.py 内新增 OTLP provider 装配分支（配置开启时惰性导入
- 暂不引入：新核心依赖（exporter 进 extras）、连接池/重试定制、TLS 自定义
- 不做的抽象/配置化：不做通用「exporter 插件」接口；不做 per-exporter 采样

### 本轮明确不做的内容
- gRPC exporter、指标/日志信号、采样策略配置、导出重试策略定制
- 任何 Langfuse 专属语义（就是标准 OTLP）

## 变更边界
### 本轮主要改动模块
- 见 tasks.md 的 boundary 记录

### 明确未触碰的区域
- gRPC exporter、指标/日志信号、采样策略配置、导出重试策略定制
- 任何 Langfuse 专属语义（就是标准 OTLP）

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
- 日期时间：2026-10-27 12:29
- 范围：可选 OTLP 导出端点（Langfuse 等外挂观测）
- 功能：OTLP 导出与降级路径（telemetry.otlp 配置三键、exporter 惰性导入进 extras、；文档同步与全量回归
- 操作：提交 / 推送
- 成效：Development Record 行为门禁通过
- 提交：pending local commit
- 提交区间：2d9cd846eb23ea51f2fce3c5b13a2dd8ae6530cb..2d9cd846eb23ea51f2fce3c5b13a2dd8ae6530cb
- 推送：not-run (push stage follows commit)

## AGENTS.md 增量提议
```diff
进程级一次性接线状态（如 telemetry/otlp.py 的 attempted/warned 旗标与解析后的配置面）必须在 tests/conftest.py 加 autouse reset fixture：否则一个测试经 load_config 读到的 otlp.endpoint 会泄漏进后续任何开 span 的测试，把死端点接进无关断言（来源：2026-10-27_add-otlp-export 评审修复轮）
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
