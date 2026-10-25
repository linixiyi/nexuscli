# 工作台鉴权重构（一次性口令 + Cookie 会话） - 完成总结

## 交付结论
- 结果：完成
- 完成时间：2026-10-25 17:10

## 假设回顾
### 已验证假设
- 单会话语义（进程生命周期即会话生命周期，重启即新一轮口令）可接受——kitagent 同款
- api_key 头通道保留不破坏 docs/runtime-api.md 的外部接入契约与既有测试

### 仍未完全验证的假设
- 无

## 交付范围
### 已交付
- runtime/websession.py + bootstrap 端点 + 会话门
- CSRF 强制与既有通道适配
- 删 key 门，#bootstrap 自动换发 + csrf 内存态
- 全量回归 + 一键链接 smoke
- 计划同步：勾选 master F14/F15 + .spec/autorun/ 摘要

### 未交付
- 无

### 偏差说明
- 无

## 简化决策
### 保持简单的关键选择
- 最简单可行方案：新模块 runtime/websession.py（三秘密生成 + authorize）+ api.py 一个 bootstrap 端点 + _handle 门改造；前端 App 加载逻辑替换 key 门
- 暂不引入：cookie 轮换、多实例、持久会话
- 不做的抽象/配置化：口令长度/会话时长不做 config 项（模块常量）

### 本轮明确不做的内容
- 多用户/多会话 cookie；token 持久化与刷新；CSP 响应头（挂账，kitagent 有、本轮不做）；docs/runtime-api.md 面改动

## 变更边界
### 本轮主要改动模块
- 见 tasks.md 的 boundary 记录

### 明确未触碰的区域
- 多用户/多会话 cookie；token 持久化与刷新；CSP 响应头（挂账，kitagent 有、本轮不做）；docs/runtime-api.md 面改动

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
- 日期时间：2026-10-25 17:10
- 范围：工作台鉴权重构（一次性口令 + Cookie 会话）
- 功能：runtime/websession.py + bootstrap 端点 + 会话门；CSRF 强制与既有通道适配；删 key 门，#bootstrap 自动换发 + csrf 内存态；全量回归 + 一键链接 smoke；计划同步：勾选 master F14/F15 + .spec/autorun/ 摘要
- 操作：提交 / 推送
- 成效：Development Record 行为门禁通过
- 提交：pending local commit
- 提交区间：2b3727ae21ad0d4cf1e1c4d7e5af7d0e30ca0240..b49126d992c2dc98da5413e522dd5f974e5aabba
- 推送：not-run (push stage follows commit)

## AGENTS.md 增量提议
```diff
- 工作台鉴权四件套（kitagent 模型）：一次性 bootstrap 口令换发 HttpOnly SameSite=strict cookie + csrf 内存态 + POST 强制 x-csrf-token + Origin 白名单；api_key 头通道保留为外部自动化豁免。新增鉴权面先看 tests/test_runtime_websession.py 的七场景。
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
