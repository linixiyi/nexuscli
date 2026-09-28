# Agent 平台能力移植（hooks/子代理/权限规则/命令增强/compact/plan 模式） - 完成总结

## 交付结论
- 结果：完成
- 完成时间：2026-09-28 13:45

## 假设回顾
### 已验证假设
- 参考渠道为 OpenCode 官方开源仓库文档与 Claude Code 公开文档（code.claude.com/docs）；不逐行复制 Claude Code 专有实现，按 nexusCLI 既有架构与代码风格原创实现等价能力。
- hooks 与权限规则配置复用现有 config.json 分层机制（内置默认 → user → project → .env → CLI → env），不引入新的配置文件格式。
- 子代理复用主 Agent 的 LLM client 与 config，不做独立模型路由（Claude Code 的 per-agent model 简化为后续扩展）。

### 仍未完全验证的假设
- 无

## 交付范围
### 已交付
- 固化问题定义、关键假设与非目标
- 从主分支创建独立工作分支
- F1 权限规则引擎（permissions allow/deny/ask）
- F2 hooks 生命周期钩子系统
- F3 子代理 task 工具
- F4 slash 命令增强（frontmatter/位置参数/shell 注入）
- F5 /compact 手动压缩命令
- F6 REPL plan 模式三态循环
- 全链路集成与 REPL smoke
- 文档同步（README/TUTORIAL//help）
- 完成验收检查并补齐证据

### 未交付
- 无

### 偏差说明
- 无

## 简化决策
### 保持简单的关键选择
- 最简单可行方案：每项功能都在既有扩展点内实现（executor 审批链插权限规则与 hooks、Agent 事件环插生命周期 hooks、tools 注册表加 task 工具、slash_commands 解析器扩 frontmatter、repl 加 /compact 与三态切换），不新建子系统、不加运行时依赖。
- 暂不引入：新配置文件格式、hook 非命令处理器、子代理模型路由、异步 hook 队列。
- 不做的抽象/配置化：权限规则引擎不做成通用 DSL（固定 tool(pattern) 一种语法）；hooks 不做插件包发现机制。
- 为什么本轮不做更多：六项功能彼此独立、共同构成"平台能力"最小闭环；其余候选（LSP、output styles 等）单项价值低或依赖重，留待后续任务包。

### 本轮明确不做的内容
- LSP 诊断深度集成（现有 py_compile 存根维持原状）
- output styles / 系统提示词人格预设
- 会话 fork/share、云同步
- models.dev 模型目录集成
- TUI 内联 diff 编辑器、fuzzy finder、statusline、桌面通知
- undo/redo（已有快照 + /restore 覆盖）
- hooks 的 http/mcp_tool/prompt 型处理器
- 子代理 per-agent model 路由与并行 fan-out 调度（team 模式已覆盖部分场景）
- 插件系统/事件总线（hooks 覆盖其核心价值）

## 变更边界
### 本轮主要改动模块
- 见 tasks.md 的 boundary 记录

### 明确未触碰的区域
- LSP 诊断深度集成（现有 py_compile 存根维持原状）
- output styles / 系统提示词人格预设
- 会话 fork/share、云同步
- models.dev 模型目录集成
- TUI 内联 diff 编辑器、fuzzy finder、statusline、桌面通知
- undo/redo（已有快照 + /restore 覆盖）
- hooks 的 http/mcp_tool/prompt 型处理器
- 子代理 per-agent model 路由与并行 fan-out 调度（team 模式已覆盖部分场景）
- 插件系统/事件总线（hooks 覆盖其核心价值）

## 验证证据
### 构建
- 适用外：本任务没有独立构建步骤

### 测试
- 脚本验证：`python check_spec_package.py --slug 2026-09-28_add-agent-platform-features --compact` → 收敛（11/11 任务完成，回填后复跑）
- 测试：`uv run python -m pytest` → 226 passed in 17.40s；`uv run python -m ruff check .` → All checks passed!；`uv run python -m ruff format --check .` → 104 files already formatted
- 构建：`uv run nexuscli --help` → exit 0

### 手工验证
- 适用外：脚本证据覆盖验收路径

## 哲学生效证据
### 行为成效回填
- Development Record 行为门禁通过

### 一致性门禁回顾
- 跨载体一致性 checklist 已通过

## Git 记录
- 日期时间：2026-09-28 13:45
- 范围：Agent 平台能力移植（hooks/子代理/权限规则/命令增强/compact/plan 模式）
- 功能：固化问题定义、关键假设与非目标；从主分支创建独立工作分支；F1 权限规则引擎（permissions allow/deny/ask）；F2 hooks 生命周期钩子系统；F3 子代理 task 工具；F4 slash 命令增强（frontmatter/位置参数/shell 注入）；F5 /compact 手动压缩命令；F6 REPL plan 模式三态循环；全链路集成与 REPL smoke；文档同步（README/TUTORIAL//help）；完成验收检查并补齐证据
- 操作：提交 / 推送
- 成效：Development Record 行为门禁通过
- 提交：pending local commit
- 推送：not-run (push stage follows commit)

## 知识沉淀
- 无

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
      "disposition": "resolved_current",
      "evidence": "hooks/runner.py 对非法 timeout 兜底 60s；tests/test_hooks.py 覆盖；全量 226 passed",
      "id": "sec-hooks-timeout",
      "summary": "hooks timeout 配置 null/零/负值导致 wait_for 无限等待，违反 timeout 强制红线",
      "taskId": "task-hooks"
    },
    {
      "actionable": true,
      "disposition": "resolved_current",
      "evidence": "agent.py 构造参数透传深度；subagent.py 深度+1 与闸门；tests/test_subagent.py 覆盖",
      "id": "sec-subagent-depth",
      "summary": "子代理深度防护因 ToolContext 未透传 subagent_depth 失效，白名单含 task 可无限递归",
      "taskId": "task-subagent"
    },
    {
      "actionable": true,
      "disposition": "resolved_current",
      "evidence": "executor.py force_prompt 强制 ask 命中走审批回调；tests/test_permissions.py 覆盖",
      "id": "sec-permissions-ask-auto",
      "summary": "permissions.ask 在默认 auto 态对 requires_approval=False 工具被自动放行（fail-open）",
      "taskId": "task-permissions"
    },
    {
      "actionable": true,
      "disposition": "resolved_current",
      "evidence": "slash_commands.py 以 CommandGuard(policy.command_blacklist) 构造注入执行器，与 bash 工具同源；tests/test_slash_commands.py 覆盖",
      "id": "sec-injection-blacklist",
      "summary": "斜杠命令 !cmd 注入执行器未套用 policy.command_blacklist，形成比 bash 工具更宽的旁路",
      "taskId": "task-slash-commands"
    },
    {
      "actionable": true,
      "disposition": "resolved_current",
      "evidence": "agent.py 仅在字段为空时写入会话戳；subagent.py 显式传 session_id",
      "id": "sec-session-id-overwrite",
      "summary": "子代理构建时覆盖共享 config 的 session_id 会话戳，hook 载荷关联错乱",
      "taskId": "task-subagent"
    },
    {
      "actionable": true,
      "disposition": "resolved_current",
      "evidence": "permission_rules.py subject_for 不再把 query 解析为主机名；tests/test_permissions.py 覆盖",
      "id": "sec-websearch-domain",
      "summary": "web_search 的 domain 规则把整句 query 误当主机名，deny 规则永不命中（fail-open）",
      "taskId": "task-permissions"
    },
    {
      "actionable": true,
      "dependency": "真人交互式 TTY 会话，自动化测试无法替代按键流",
      "disposition": "external_blocked",
      "id": "repl-manual-smoke",
      "owner": "用户",
      "retryTrigger": "下次真人使用 REPL 时按 checklist 手工验证栏执行",
      "summary": "交互式 REPL 手工冒烟（Shift+Tab 三态、/help 渲染、/compact 实机输出）待真人终端执行"
    }
  ],
  "version": 1
}
```
