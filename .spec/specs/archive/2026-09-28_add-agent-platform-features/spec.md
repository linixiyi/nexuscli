# Agent 平台能力移植（hooks/子代理/权限规则/命令增强/compact/plan 模式） - 项目范围

## 1. 问题定义
- **项目目标**：参考 OpenCode（MIT 开源）与 Claude Code（公开文档及社区公开分析）的平台级能力设计，为 nexusCLI 补齐六项缺失的 Agent 平台功能：hooks 生命周期钩子、子代理 task 工具、权限规则、slash 命令增强、/compact 手动压缩、REPL plan 模式循环。
- **目标用户**：使用 nexusCLI 进行真实项目开发的终端用户（需要自动化拦截、并行委派、免审批白名单、可定制命令的开发者）。
- **核心价值**：让 nexusCLI 从"单一 ReAct 循环"升级为可治理、可扩展的 Agent 平台：策略可编程（hooks/permissions）、算力可横向扩展（子代理）、交互可定制（命令增强/plan 模式）、上下文可控（compact）。

## 2. 假设与待确认

### 2.1 已确认事实
- nexusCLI 现有架构：`Agent`（react/plan/team 三模式，流式事件协议）、`ToolExecutor`（HITL 审批 + JSONL 审计 + 只读并发）、`ContextWindowManager`（80% 阈值压缩至 55%）、三层记忆、Skill 系统、快照、会话恢复、Runtime API。
- REPL 已有 Shift+Tab 两态切换（Default/Auto），命令分发为 `repl.py` 中 if/elif 链；自定义 slash 命令仅支持 `description` frontmatter 与 `$ARGUMENTS`。
- 当前无 hooks 系统、无子代理工具、无权限规则引擎、无 `/compact`、无 plan 只读模式；权限只有 HITL（never/auto/always）+ command_guard/path_guard 静态分类。
- `lsp/diagnostics.py` 仅为 py_compile 存根；本地无 OpenCode/Claude Code 源码副本。
- 测试基线：140 tests 全绿，ruff check/format 通过（基线提交 415cb24）。

### 2.2 关键假设
- 参考渠道为 OpenCode 官方开源仓库文档与 Claude Code 公开文档（code.claude.com/docs）；不逐行复制 Claude Code 专有实现，按 nexusCLI 既有架构与代码风格原创实现等价能力。
- hooks 与权限规则配置复用现有 config.json 分层机制（内置默认 → user → project → .env → CLI → env），不引入新的配置文件格式。
- 子代理复用主 Agent 的 LLM client 与 config，不做独立模型路由（Claude Code 的 per-agent model 简化为后续扩展）。

### 2.3 待确认问题
- ~~hooks 的 http/mcp_tool/prompt 型处理器是否实现~~ → 已解决（本轮只实现 command 型，其余见 3.3 范围外）。
- ~~子代理是否需要 per-agent model 覆盖~~ → 已关闭（本轮不做 model 路由，frontmatter 仅预留字段，见 3.3 范围外）。

### 2.4 可选解释与取舍
- 当前选择：hooks 配置放 config.json `hooks` 键 -> 理由：复用既有分层合并与 env 覆盖管线，避免新增文件解析；Claude Code 用独立 settings.json 属于其生态惯例，非功能必需。
- 当前选择：权限规则语法 `tool(pattern)`（如 `bash(git diff:*)`、`write_file(src/**)`）-> 理由：兼容 Claude Code 社区心智；OpenCode 的按工具键值对形式可作为等价表达，不双轨支持。
- 当前选择：plan 模式在 executor 层拒绝非只读工具 -> 理由：比"提示词约束"更强且可测试；与 Shift+Tab 既有循环自然衔接。

## 3. 功能范围

### 3.1 核心功能（MVP）
- [x] F1 权限规则：config `permissions.allow/deny/ask` 模式规则，在 HITL 之前评估；deny 直接拒绝、allow 跳过审批、ask 强制审批；审计记录规则命中。
- [x] F2 hooks 系统：SessionStart/UserPromptSubmit/PreToolUse/PostToolUse/Stop 五事件，command 型处理器（stdin JSON、exit 2 阻断、stdout JSON decision、matcher、timeout），config `hooks` 键分层合并。
- [x] F3 子代理 task 工具：react 内置 `task` 工具，内置 general-purpose/explore 类型 + `.nexuscli/agents/*.md` 自定义代理（name/description/tools），独立会话与 Skill 缓冲，防递归（深度 1），返回最终报告。
- [x] F4 slash 命令增强：frontmatter `mode`/`allowed-tools`/`argument-hint`，位置参数 `$1..$9`，`` !`cmd` `` shell 注入。
- [x] F5 /compact 命令：REPL 手动压缩当前历史，可选 focus 主题，报告前后 token。
- [x] F6 plan 模式循环：Shift+Tab 三态 default → auto → plan；plan 态拒绝一切非只读工具调用并提示。

### 3.2 扩展功能
- 无（本轮全部为 MVP 边界内交付）

### 3.3 不在范围内
- LSP 诊断深度集成（现有 py_compile 存根维持原状）
- output styles / 系统提示词人格预设
- 会话 fork/share、云同步
- models.dev 模型目录集成
- TUI 内联 diff 编辑器、fuzzy finder、statusline、桌面通知
- undo/redo（已有快照 + /restore 覆盖）
- hooks 的 http/mcp_tool/prompt 型处理器
- 子代理 per-agent model 路由与并行 fan-out 调度（team 模式已覆盖部分场景）
- 插件系统/事件总线（hooks 覆盖其核心价值）

## 4. 最小实现路径
- 最简单可行方案：每项功能都在既有扩展点内实现（executor 审批链插权限规则与 hooks、Agent 事件环插生命周期 hooks、tools 注册表加 task 工具、slash_commands 解析器扩 frontmatter、repl 加 /compact 与三态切换），不新建子系统、不加运行时依赖。
- 暂不引入：新配置文件格式、hook 非命令处理器、子代理模型路由、异步 hook 队列。
- 不做的抽象/配置化：权限规则引擎不做成通用 DSL（固定 tool(pattern) 一种语法）；hooks 不做插件包发现机制。
- 为什么本轮不做更多：六项功能彼此独立、共同构成"平台能力"最小闭环；其余候选（LSP、output styles 等）单项价值低或依赖重，留待后续任务包。

## 5. 技术决策
- 技术栈：Python 3.11+，仅标准库 + 既有依赖（httpx/openai 兼容层、rich、prompt_toolkit），零新增运行时依赖。
- 本轮允许改动：`src/nexuscli/config.py`、`policy/`、`tools/`（executor/registry/builtins/base）、`agent/`（agent.py 生命周期接入）、`entrypoints/`（repl.py/slash_commands.py/cli.py）、`context/manager.py`（compact 入口）、`tests/`、`README.md`/`TUTORIAL.md`、`.spec/`。
- 本轮不应触碰：`llm/` 协议层与流式解析、`mcp/`、`runtime/` 核心、`session/`、`snapshot/`、`web/`、`image/`、`plan/`、`rag/`、`sdk.py`。
- Git integration branch：`spec/2026-09-28_add-agent-platform-features`

### 5.4 编排策略
- route: build
- immediate blocker: 包状态管理、任务路由、车道指派契约、合并与验收门禁留在主线程；实现代码全部由受约束 sidecar 车道完成（用户明确要求主会话只做规划与安排）
- ownership: 按文件所有权分波次——Wave1 并行：权限规则（policy/config/executor）、slash 解析（slash_commands.py）、compact 入口（context/manager.py）；Wave2 并行：hooks（hooks/ 新包+executor+agent+repl+config）、子代理（tools/base+builtins+新 subagent 模块）；Wave3：plan 模式（executor+repl，须在 Wave2 合并后）；Wave4：集成粘合（repl 接线 D/E + 六功能缝隙）→ 安全评审车道（risks 非空：信任边界/密钥）+ 文档车道；每波合并后主线程跑全量 pytest+ruff
- waiting strategy: 车道间存在 executor.py/config.py/repl.py 写冲突的必须跨波串行；同波内文件集两两不相交
- verification gate: 每车道自跑其 verify 命令；主线程合并后跑全量 `uv run python -m pytest -q` + ruff check + ruff format --check；安全评审车道结论回写后才能进入验收

## 6. 成功标准与验证方式
- 标准 A：F1-F6 各有单元/集成测试且通过 -> verify: `uv run python -m pytest -q` 全绿（新增测试 ≥ 15 个）
- 标准 B：六项功能在 REPL 中可用 -> verify: `uv run nexuscli --help` 正常 + 手动 smoke（/help 列出新命令、/compact 输出压缩统计）
- 标准 C：既有行为无回归 -> verify: pytest 全量 + ruff check + ruff format --check 通过
- 标准 D：配置向后兼容 -> verify: 旧 config.json（无 hooks/permissions 键）启动无报错，既有测试不修改断言即通过

## 7. 风险与约束
- 风险点：hooks 执行用户 shell 命令引入安全面 -> 缓解：默认未配置即不启用；timeout 强制；PreToolUse 只允许阻断不允许放行绕过 HITL（hook allow 不跳过 requires_approval 工具的审批，只允许 deny/additionalContext）。
- 风险点：子代理与主循环共享 executor 导致审批/HITL 语义混乱 -> 缓解：子代理走同一 ToolExecutor 与 policy，审批回调透传，权限规则与 plan 模式约束同等生效。
- 风险点：slash 命令 `` !`cmd` `` shell 注入滥用 -> 缓解：注入命令经 classify_command，危险类直接拒绝并在展开结果中注明。
- 风险点：Mimosa 预提交钩子拦截（测试占位值、SQL 形态）-> 缓解：新增代码遵循已验证的安全写法（env 间接引用凭据、字面量 SQL / 常量 f-string 习语）。
- 约束：Windows（win32/Git Bash）为主开发环境，hook 命令执行需兼容（shell=True 走系统 shell）。
