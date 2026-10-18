# NexusCLI 真实使用场景审计与缺口修复 - 完成总结

## 交付结论
- 结果：delivered
- 完成时间：2026-10-18 15:10

## 假设回顾
### 已验证假设
- 评审以「一个真实开发者每天会做的事」为准：读代码、改代码、跑命令、切模型/力度、断点续跑、看用量、出错恢复。
- 真实 LLM 网关是本机沙箱资源，跑失败可重试；不引入任何新的外部依赖。
- 修法以最小可逆改动为主，不重排模块结构；观感层不回归已确认的单色前景约束。

### 仍未完全验证的假设
- 无

## 交付范围
### 已交付
- 真实使用场景审计（REPL/TUI、工具面权限、联网平台三路只读子代理，48 条发现）并修复 11 条：Ctrl+C 杀进程、审批提示无法退出、写盘工具静默覆盖、只读直通外带私钥、正文换行被折叠、-p 拒绝信息拼写与指路、pytest 污染真实 audit.jsonl、-p --mode plan 未开只读闸门、/effort 假装生效、MCP initialize 不合规、web_fetch 误杀 fake-IP。

### 未交付
- 无

### 偏差说明
- 无

## 简化决策
### 保持简单的关键选择
- 最简单可行方案：按审计清单逐条修，每条配一条能真实跑出来的验证命令（测试或 CLI 冒烟），最后跑全量 pytest + ruff + 一次真实 `-p` 冒烟。
- 暂不引入：新的配置项、新的抽象层、跨平台打包改动。
- 不做的抽象/配置化：不把审批策略重构成可插拔后端，只修语义与文案。

### 本轮明确不做的内容
- 不改事件流、审批四档语义、会话转录格式、usage 台账口径
- 不重排 `src/nexuscli` 目录结构；不做观感层重设计
- 不引入 OS 级沙箱

## 变更边界
### 本轮主要改动模块
- 见 tasks.md 的 boundary 记录

### 明确未触碰的区域
- 不改事件流、审批四档语义、会话转录格式、usage 台账口径
- 不重排 `src/nexuscli` 目录结构；不做观感层重设计
- 不引入 OS 级沙箱

## 验证证据
### 备注
- 测试：889 passed, 3 skipped；ruff check 与 format --check 全绿；runs.jsonl 13 条机器任务全部 exit=0
- 手工：真实网关 -p/plan/team 三模式跑通；官方 MCP SDK 客户端握手通过；web_fetch 取回 GitHub 与 python.org 正文

## 验证范围
- 级别：package
- 机器验证率：12/12；manual 任务 5 项不计入分母
- 规则：check 与 done 复用该范围；只有记录升级触发时才执行全项目检查

## 哲学生效证据
### 行为成效回填
- Development Record 行为门禁通过

### 一致性门禁回顾
- 验收面（checklist 或成功标准）已通过

## Git 记录
- 日期时间：2026-10-18 15:10
- 范围：NexusCLI 真实使用场景审计与缺口修复
- 功能：真实使用场景审计（REPL/TUI、工具面权限、联网平台三路只读子代理，48 条发现）并修复 11 条：Ctrl+C 杀进程、审批提示无法退出、写盘工具静默覆盖、只读直通外带私钥、正文换行被折叠、-p 拒绝信息拼写与指路、pytest 污染真实 audit.jsonl、-p --mode plan 未开只读闸门、/effort 假装生效、MCP initialize 不合规、web_fetch 误杀 fake-IP。
- 操作：提交 / 推送
- 成效：Development Record 行为门禁通过
- 提交：pending local commit
- 提交区间：26bea2e85c7c4441a5cc00dc43370eb51e43ac39..26bea2e85c7c4441a5cc00dc43370eb51e43ac39
- 推送：not-run (push stage follows commit)

## AGENTS.md 增量提议
```diff
NexusCLI 的 pty 测试必须自建 pty + 应答 ESC[6n（CPR），裸 pexpect 会整屏错位，结论会假。
prompt_toolkit 在 prompt_async() 返回时会 remove_signal_handler，SIGINT 回落 SIG_DFL；长任务期间必须重新 add_signal_handler + task.cancel，raise KeyboardInterrupt 会被 asyncio.run 转成 SystemExit(130)。
判断只读命令不能只看命令名：head/cat 指向 ~/.ssh、~/.nexuscli、.env 时等同外带凭证，必须并入审批链。
rich.Markdown 把单个换行当软换行，助手正文必须补 CommonMark 硬换行（两空格），否则多行输出在终端里塌成一段。
MCP server 的 initialize 必须返回 protocolVersion/capabilities/serverInfo.version，curl 能过不代表官方 SDK 客户端能过。
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
  "issues": [
    {
      "actionable": true,
      "disposition": "accepted_risk",
      "id": "AUDIT-2026-10-18-RESIDUAL",
      "owner": "next spec round (spec:autorun)",
      "rationale": "本轮 scope 已锁在 11 条最高优先缺陷，其余 11 条每条都需要跨模块设计（进程组、连接池、状态栏布局、压缩预算）而非单点修复；已逐条写入 .spec/plans/README.md 的 29 条 feature checkbox，由 /spec:autorun 链消费，不会丢失",
      "reviewTrigger": ".spec/plans/README.md 中 A1 被勾选时复核本条",
      "summary": "审计剩余的 11 条缺陷（-p 审批回调、审计日志脱敏、MCP 浏览器安全、Runtime 审批、进程组超时、窄终端状态栏、Tab 补全、/compact 预算、MCP 连接复用等）转入下一轮收口计划"
    }
  ],
  "version": 1
}
```
