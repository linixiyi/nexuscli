# NexusCLI 评审残留修复 - 完成总结

## 交付结论
- 结果：2 项残留修复通过门禁独立复跑与 spec_verify 亲跑（runs.jsonl 2/2 exit 0，task_0002 含 e2e 12 passed）；全仓 lint exit 0
- 完成时间：2026-11-08 14:57

## 假设回顾
### 已验证假设
- A1 | gate_denial 转发与 `_team_result_from_events` 机制已在 main 可复用 | system | 收尾轮 M1 修复记录与 main HEAD 代码实读 | CONFIRMED | if false: 本包退化为重新实现转发机制，工作量加倍
- A2 | 两项修复不影响 repl 等其他外层事件消费方 | system | 外层事件流消费方仅 cli 与 runtime/api，均有 scoped 回归覆盖 | UNVERIFIED | if false: 按 regression 结果扩大 boundary 并记录偏离

### 仍未完全验证的假设
- 无

## 交付范围
### 已交付
- 0001 plan worker 的嵌套 plan_failed 转发到外层事件流（原挂账一）
- 0002 runtime 事件折叠放行 gate_denial 并在前端展示（原挂账二）

### 未交付
- 无

### 偏差说明
- 无

## 简化决策
### 保持简单的关键选择
- 最简单可行方案：两任务并行——0001 复用收尾轮 M1 的 gate_denial 转发模式补 plan_failed 转发；0002 在 runtime 折叠白名单放行 gate_denial 并在 derive.ts 展示。
- 暂不引入：事件溯源、前端状态管理库、新的配置项。
- 不做的抽象：不为单一事件类型建通用转发注册表（单一实现者阶段保持直连）。
- B0 | literal ask | 两任务最小接线修复，覆盖 headless 与 workbench 两条用户可见路径 | 用户指令清掉剩余可做项；两项残留的位置与修法已在收尾轮复核记录写明
- B1 | minimal change | 只修 headless 路径（0001），workbench 展示留待后续 | 交付更快，但 workbench 用户仍看不到门拒绝，不满足「清掉可做项」的字面范围
- B2 | recommended alternative | 两项都修且顺带把事件折叠重写为注册表 | 覆盖面相同但引入不必要的重构，违反最小接线取舍

### 本轮明确不做的内容
- 不处理真实模型 eval、Windows 沙箱等 3.2 扩展功能；不做事件溯源式重写。

## 变更边界
### 本轮主要改动模块
- 见 tasks.md 的 boundary 记录

### 明确未触碰的区域
- 不处理真实模型 eval、Windows 沙箱等 3.2 扩展功能；不做事件溯源式重写。

## 验证证据
### 备注
- 脚本验证：`uv run pytest -q tests/test_headless_status.py tests/test_multi_agent.py` -> exit 0 (runs:task_0001)
- 脚本验证：`uv run pytest -q tests/test_runtime_sse.py tests/test_runtime_approvals.py && npm --prefix frontend run test:e2e` -> exit 0 (runs:task_0002)

## 验证范围
- 级别：package
- 机器验证率：2/2
- 规则：check 与 done 复用该范围；只有记录升级触发时才执行全项目检查

## 哲学生效证据
### 行为成效回填
- Development Record 行为门禁通过

### 一致性门禁回顾
- 验收面（checklist 或成功标准）已通过

## 失误方向统计
- DEFERENCE：0
- OVERREACH：0
- KNOWLEDGE：0
- PROCESS：0
- 只统计，不自动调整 Autonomy。

## Git 记录
- 日期时间：2026-11-08 14:57
- 范围：NexusCLI 评审残留修复
- 功能：0001 plan worker 的嵌套 plan_failed 转发到外层事件流（原挂账一）；0002 runtime 事件折叠放行 gate_denial 并在前端展示（原挂账二）
- 操作：提交 / 推送
- 成效：Development Record 行为门禁通过
- 提交：pending local commit
- 提交区间：b5ad60980874ed190ab72bb21a121d23839a1a4d..3fb700020ead0c4dbd06e1096ec91c3d81908a80
- 推送：not-run (push stage follows commit)

## AGENTS.md 增量提议
```diff
+ - 嵌套事件流的内层折叠必须把结构化失败事件（plan_failed/gate_denial）转发到外层流，否则 headless 与 workbench 对被拦截运行静默报 ok；runtime 新增折叠事件类型要同步前端 derive；——来源:2026-11-08_fix-review-residuals
```

## 被拒绝的扩展提议
- 无

## 门禁证据
- 无

## 遗留事项
- 无；问题处置见下方结构化区块

## 问题处置

```json
{
  "issues": [],
  "version": 1
}
```
