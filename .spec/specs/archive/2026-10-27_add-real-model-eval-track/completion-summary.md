# 真模型 eval 轨道：pass^k + 真实成本 + LLM-as-judge - 完成总结

## 交付结论
- 结果：完成
- 完成时间：2026-10-27 06:44

## 假设回顾
### 已验证假设
- 真模型轨道与 hermetic 轨道共享场景 schema 的断言子集（final_contains/max_turns/
- judge 断言的评审模型调用走与被测 agent 相同的客户端工厂（OpenAI-compatible），评审
- 真模型轨道的报告独立成文件（.nexuscli/evals/last-real-report.json），绝不污染
- 测试全程用可注入的假 LLM/假 judge（复用 ScriptedLlmClient 注入模式）保持 hermetic；

### 仍未完全验证的假设
- 无

## 交付范围
### 已交付
- 真模型轨道执行路径与 CLI 接线（--real/--repeat、无 Key 明确报错、只读工具面、
- pass^k 聚合 + 真实成本记录 + judge 断言（rubric/阈值/评审模型覆盖/假 judge 注入）
- 文档同步与全量回归（docs/eval.md 两轨道口径重写、README 评测回归行）
- 真模型机会性冒烟（最小场景 1 轮，留成本证据）

### 未交付
- 无

### 偏差说明
- 无

## 简化决策
### 保持简单的关键选择
- 最简单可行方案：eval 模块内新增 real 轨道执行路径（与 hermetic 分派并列），复用
- 暂不引入：新依赖、评审模型独立配置体系（只做场景级 model 覆盖）、成本重试/预算熔断
- 不做的抽象/配置化：不为 pass^k 做独立命令（--repeat 足够）；不为真模型轨道做

### 本轮明确不做的内容
- hermetic 轨道的行为改动（回归基线零影响）
- 定时任务/夜间调度接入（cron 已有，接线后续按需）
- 并发跑重复轮次（k 次串行即可）、结果缓存、多评审模型投票

## 变更边界
### 本轮主要改动模块
- 见 tasks.md 的 boundary 记录

### 明确未触碰的区域
- hermetic 轨道的行为改动（回归基线零影响）
- 定时任务/夜间调度接入（cron 已有，接线后续按需）
- 并发跑重复轮次（k 次串行即可）、结果缓存、多评审模型投票

## 验证证据
### 备注
- 手工：机会性真模型冒烟由实现工作流执行：退出码 1（环境未配置可用 Provider Key，无 Key 明确报错正是设计行为）；轨道逻辑由假 LLM/假 judge 注入测试 49 用例全量覆盖。另：全量 pytest 曾现 1 例既有竞态，干净 HEAD worktree 全量 exit 0 实证非本包引入，本工作树复跑两次均全绿

## 验证范围
- 级别：package
- 机器验证率：3/3；manual 任务 1 项不计入分母
- 规则：check 与 done 复用该范围；只有记录升级触发时才执行全项目检查

## 哲学生效证据
### 行为成效回填
- Development Record 行为门禁通过

### 一致性门禁回顾
- 验收面（checklist 或成功标准）已通过

## Git 记录
- 日期时间：2026-10-27 06:44
- 范围：真模型 eval 轨道：pass^k + 真实成本 + LLM-as-judge
- 功能：真模型轨道执行路径与 CLI 接线（--real/--repeat、无 Key 明确报错、只读工具面、；pass^k 聚合 + 真实成本记录 + judge 断言（rubric/阈值/评审模型覆盖/假 judge 注入）；文档同步与全量回归（docs/eval.md 两轨道口径重写、README 评测回归行）；真模型机会性冒烟（最小场景 1 轮，留成本证据）
- 操作：提交 / 推送
- 成效：Development Record 行为门禁通过
- 提交：pending local commit
- 提交区间：24861db266c9f2fe2c4562488ba92c2d93195a6d..24861db266c9f2fe2c4562488ba92c2d93195a6d
- 推送：not-run (push stage follows commit)

## AGENTS.md 增量提议
```diff
真模型/无人值守轨道的只读工具白名单必须按名精选而非按 is_read_only 旗标推导：todo_write 之类工具自带 is_read_only=True 默认旗标却有落盘副作用（.nexuscli/todo.json），旗标推导会放进写通道（来源：2026-10-27_add-real-model-eval-track REAL_TRACK_TOOLS 设计）
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
