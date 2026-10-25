# 回合级恢复与引擎结果合同（F10/F11） - 完成总结

## 交付结论
- 结果：完成
- 完成时间：2026-10-25 14:27

## 假设回顾
### 已验证假设
- checkpoint 明文落盘沿用 plan_runs 口径；REPL Ctrl+C 仍是中止（价值=从最近完整轮接续）
- F7 的 SSE/REPL 渲染事件流协议一字不动

### 仍未完全验证的假设
- 无

## 交付范围
### 已交付
- src/nexuscli/agent/results.py：EngineResult 合同与错误码
- 发射点 error_code 与 plan/team 终态聚合
- src/nexuscli/agent/turn_checkpoint.py：存储与列表
- _run_react 双快照点 + /turn-resume 接线
- 全量回归（CI 三步）
- 计划同步：勾选 master F10/F11 + .spec/autorun/ 摘要

### 未交付
- 无

### 偏差说明
- 无

## 简化决策
### 保持简单的关键选择
- 最简单可行方案：turn_checkpoint.py 单文件存储 + agent.py 两处快照写 + repl.py 一条命令；
- 暂不引入：checkpoint 压缩/加密、TTL 清理（沿用 plan_runs 无清理口径，文档说明）、
- 不做的抽象/配置化：checkpoint 开关不做 config 项（Agent 参数 opt-in，REPL 开启）

### 本轮明确不做的内容
- approval_pending 生产者（挂账：F6 同步阻塞回调无挂起出口）；顶层 --resume-turn flag；
- checkpoint 加密（沿用 plan_runs 明文口径，文档如实说明）

## 变更边界
### 本轮主要改动模块
- 见 tasks.md 的 boundary 记录

### 明确未触碰的区域
- approval_pending 生产者（挂账：F6 同步阻塞回调无挂起出口）；顶层 --resume-turn flag；
- checkpoint 加密（沿用 plan_runs 明文口径，文档如实说明）

## 验证证据
### 备注
- 机器证据：runs.jsonl（见验证范围的机器验证率）

## 验证范围
- 级别：package
- 机器验证率：6/6
- 规则：check 与 done 复用该范围；只有记录升级触发时才执行全项目检查

## 哲学生效证据
### 行为成效回填
- Development Record 行为门禁通过

### 一致性门禁回顾
- 验收面（checklist 或成功标准）已通过

## Git 记录
- 日期时间：2026-10-25 14:27
- 范围：回合级恢复与引擎结果合同（F10/F11）
- 功能：src/nexuscli/agent/results.py：EngineResult 合同与错误码；发射点 error_code 与 plan/team 终态聚合；src/nexuscli/agent/turn_checkpoint.py：存储与列表；_run_react 双快照点 + /turn-resume 接线；全量回归（CI 三步）；计划同步：勾选 master F10/F11 + .spec/autorun/ 摘要
- 操作：提交 / 推送
- 成效：Development Record 行为门禁通过
- 提交：pending local commit
- 提交区间：e0942728587e6e55e27b39e60d47d863787e6322..8f1c6417f0d2be267351888851fb4df5afee0f83
- 推送：not-run (push stage follows commit)

## AGENTS.md 增量提议
```diff
- 回合级恢复的三件套语义：_run_react 双快照点（in-flight 含 pending 原始 id / 轮末覆盖为空）+ /turn-resume 沿用原 run_id + journal 按原 id 分流（completed 复用、started/outcome_unknown fail-closed）——新增恢复类功能时三件必须一起动；EngineResult 折叠层的预算耗尽靠 warning 事件的 kind 标记，不做消息字符串匹配。
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
