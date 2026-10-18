# NexusCLI 审计遗留收口：安全、进程、渲染、平台 - 完成总结

## 交付结论
- 结果：完成
- 完成时间：2026-10-18 17:21

## 假设回顾
### 已验证假设
- 每条修复都能用「一条能真实跑出来的验证命令」证明，不靠"应该可以"。
- 进程组、连接池这类跨平台改动以 POSIX 为主路径，Windows 走降级分支并如实写边界。

### 仍未完全验证的假设
- 无

## 交付范围
### 已交付
- A1-A4 安全审批四项；B1-B3 进程超时三项；C1-C11 渲染交互十一项；D1-D9 平台文档九项；E1 测试矩阵文档

### 未交付
- 无

### 偏差说明
- 无

## 简化决策
### 保持简单的关键选择
- 最简单可行方案：按 `.spec/plans/README.md` 的 checkbox 逐条修，每条配回归测试；跨模块项（进程组、连接池）先写测试再改实现。
- 暂不引入：新的配置抽象层、插件化后端。
- 不做的抽象/配置化：不把审批策略做成可插拔后端；不把进程管理抽成通用库。

### 本轮明确不做的内容
- 不重排 `src/nexuscli` 目录结构
- 不改事件流协议、审批四档语义
- 不引入 OS 级沙箱或新的外部服务依赖

## 变更边界
### 本轮主要改动模块
- 见 tasks.md 的 boundary 记录

### 明确未触碰的区域
- 不重排 `src/nexuscli` 目录结构
- 不改事件流协议、审批四档语义
- 不引入 OS 级沙箱或新的外部服务依赖

## 验证证据
### 备注
- 测试：948 passed / 3 skipped；ruff check + format 全绿；runs.jsonl 22 条机器验证全 exit=0
- 手工：pty 实测：48 列模式名保留 / /sk<Tab> 补全 / @image: / /clear 重绘 / /config 摘要 / /skill 未知报错 / effort off 可见；真实进程实测：-p --yes 落盘、审计 0600、MCP 405/404/415/403、runtime 写被拒、背景任务组停止后为空、bash 超时 2.06s、MCP 三调用同 pid、cron disabled/单行、端口占用可读、web_search 带原因

## 验证范围
- 级别：package
- 机器验证率：25/25；manual 任务 2 项不计入分母
- 规则：check 与 done 复用该范围；只有记录升级触发时才执行全项目检查

## 哲学生效证据
### 行为成效回填
- Development Record 行为门禁通过

### 一致性门禁回顾
- 验收面（checklist 或成功标准）已通过

## Git 记录
- 日期时间：2026-10-18 17:21
- 范围：NexusCLI 审计遗留收口：安全、进程、渲染、平台
- 功能：A1-A4 安全审批四项；B1-B3 进程超时三项；C1-C11 渲染交互十一项；D1-D9 平台文档九项；E1 测试矩阵文档
- 操作：提交 / 推送
- 成效：Development Record 行为门禁通过
- 提交：pending local commit
- 提交区间：95f77f080fc29f07d74472e81887cef64f2aec03..95f77f080fc29f07d74472e81887cef64f2aec03
- 推送：not-run (push stage follows commit)

## AGENTS.md 增量提议
```diff
进程管理：后台任务与 bash 统一 start_new_session + killpg，停止必须等进程组清空再报 stopped（否则孙进程继续跑还谎报成功）
审批链：任何需要人点头的工具都必须 requires_approval=True；-p 无审批回调时按失败关闭，并显式提供 --yes 放行
MCP：server 的 initialize 必须返回 protocolVersion/capabilities/serverInfo.version，否则官方 SDK 全部握手失败；client 会话要复用，每次新起进程会丢服务端状态
渲染：Rich Markdown 会把单换行折叠成空格，助手正文必须转硬换行；-p 路径不受影响，容易漏测
敏感路径：只读 bash 直通不能替代路径守卫，~/.ssh、~/.nexuscli、.env 等必须回落审批
测试隔离：pytest 必须重定向 HOME 与 USERPROFILE，否则审计日志/会话/用量库会被测试污染真实用户目录
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
