# NexusCLI 发布链与执行安全加固 - 完成总结

## 交付结论
- 结果：36 项机器任务全部通过 spec_verify verify 重放（runs.jsonl 36/36 exit 0）；评审轮+收尾轮确认并修复 15 项问题（含权限复合命令绕过、REPL 注入回归两个安全 high）；终检门禁 5/5：全量 pytest+coverage 棘轮 0 failed、Playwright e2e 12 passed、mypy 棘轮、ruff 双检
- 完成时间：2026-11-08 13:42

## 假设回顾
### 已验证假设
- A1 | `spec/2026-11-07_fix-nexuscli-release-hardening` 分支已创建且可提交 | system | `git branch --show-current` 返回该分支 | CONFIRMED | if false: 包无法在独立分支执行，先重建分支。
- A2 | zcode 执行环境可以运行 `uv`、`npm`、`git`，并能访问构建所需网络 | system | 本机 CI 使用 uv/npm；zcode 环境未探测 | UNVERIFIED | if false: 把构建/安装类 verify 降级为 manual，并保留 pytest 可执行部分。
- A3 | 现有 1,690 passed / 3 skipped 是稳定基线 | system | 2026-11-07 本地全量运行 | CONFIRMED | if false: 先修复基线失败，再执行本包。
- A4 | 维护者可以配置 PyPI trusted publisher 并推送 `v0.2.1` tag | external | PyPI 404、无 tag、无 release workflow；无凭据证据 | UNVERIFIED | if false: task_0006 保持 manual，发布由 owner 在本地完成。
- A5 | GitHub Actions 可以安装 Playwright 浏览器并运行 e2e | external | `frontend/package.json` 有 `test:e2e`，CI 未跑；无 workflow 证据 | UNVERIFIED | if false: e2e 只作为本地/手动门禁，task_0020 改为 manual。
- A6 | 目标 macOS/Linux 主机具备 `sandbox-exec`、`bwrap` 或 Landlock 内核支持 | external | 仓库当前无 OS 沙箱实现，docs 把 OS 级沙箱列为挂账 | UNVERIFIED | if false: 后端探测返回 unsupported，任务只交付抽象与 fail-closed 行为。
- A7 | Google Gemini 与本地 Ollama/llama.cpp 的接口可按 OpenAI-compatible 或原生 HTTP 测试 | external | 当前 provider 无 Google/Ollama 预设 | UNVERIFIED | if false: provider 任务保留适配器与 fake server 测试，真实联调转 manual。
- A8 | 把 MCP 工具统一改为不可信只读是可接受的安全取舍 | product | P0-2 的 readOnlyHint 可被 server 谎报 | UNVERIFIED | if false: 增加显式本地 `trusted_readonly` 配置，但默认仍不可信。
- A9 | 修完 lint 后，CI 其余 job（mypy/pytest/frontend）可通过 | system | 本地 pytest 通过；mypy 未在最新 HEAD 复跑；frontend job 未跑 e2e | UNVERIFIED | if false: 在对应任务中追加修复，不把 lint 绿当成 CI 全绿。
- A10 | `--mode plan` 的只读闸是有意设计；真实运行中的「全部 COMPLETED 但什么都没写」是状态机缺陷，不是 plan 模式本意 | product | `src/nexuscli/entrypoints/cli.py:242-248` 注释明确把 `--mode plan` 与 REPL 只读态对齐；真实运行 4 个任务全部空转却标 COMPLETED | CONFIRMED | if false: task_0034 改为移除 plan gate，而不是把空转标 blocked。
- A11 | workbench 的 dist 应从仓库/包根解析，不应依赖 `--cwd`（任务工作目录） | product | 真实运行 `--cwd /tmp/...` 空态、`--cwd` 仓库根正常；`runtime/api.py:1518-1522` 只查 `self.cwd/frontend/dist` | CONFIRMED | if false: task_0035 只改空态文案，不改变解析顺序。
- A12 | 未知模型的价格应显示 `unknown`/`null` 并给出配置指引，不应显示 0.0 或空对象 | product | 真实运行 `cost` 为 `{}`、`session_usage` 为 0.0；`llm/pricing.py` 只有内置模型价格表 | CONFIRMED | if false: task_0037 只加文档，不改 JSON 契约。

### 仍未完全验证的假设
- 无

## 交付范围
### 已交付
- 0001 修复仓库 lint/format 阻断，恢复 CI 绿
- 0002 将 slash `!` 注入接入 ToolExecutor 审批、审计与环境清洗
- 0003 让 MCP 的 readOnlyHint 不再产生本地可信只读
- 0004 让 wheel 自带 workbench，sdist 排除 node_modules 与 .spec
- 0005 增加 tag 触发的 PyPI release workflow 与 0.2.1 版本
- 0007 README 安装路径、社区文件与 docs contract
- 0010 让 query 与 ReAct 共享 LoopGuard 与 max_turns 撞顶提示
- 0011 让 query 与 ReAct 共享 provider 超限重试与 F12 截断保护
- 0012 plan/team worker 的 max_turns 对齐 config
- 0013 plan 每个任务接收会话历史
- 0014 team 并发 worker 使用隔离 history
- 0015 plan worker 传入 journal/run_id，恢复不重复副作用
- 0016 deny/skip 分支的审计受 audit_log 控制
- 0017 MCP server 不再就地改写共享 config
- 0018 permission prefix 规则按词边界匹配
- 0019 `-p "/命令"` 消费 frontmatter 的 mode/allowed_tools
- 0020 把 Playwright e2e 接入 CI
- 0021 workbench 支持 approve_run/approve_auto、SSE 审批与可配 timeout
- 0022 coverage baseline ratchet
- 0023 mypy ratchet
- 0024 pty SIGINT、并发恢复测试与 testing-matrix 更新
- 0025 hermetic eval 进入 CI 门禁
- 0026 OS 沙箱抽象 + macOS seatbelt
- 0027 Linux bubblewrap/Landlock 后端
- 0028 会话树：parent/branch 元数据与 `/tree`
- 0029 结构化压缩与文件台账
- 0030 插件包格式：用户级、命名空间、工具组件
- 0031 Google Gemini 原生 provider
- 0032 Ollama/llama.cpp 本地 provider
- 0033 文档站搜索与英文入口
- 0034 修复 `--mode plan` 假完成
- 0035 workbench dist 从仓库/包根解析
- 0036 headless 被拦截时可机读
- 0037 未知模型价格显式 unknown
- 0038 固定 prompt/tool 开销可诊断
- 0039 明确 `--mode plan` 的只读语义

### 未交付
- 无

### 偏差说明
- 无

## 简化决策
### 保持简单的关键选择
- 最简单可行方案：按阶段 A → B → C → D 顺序执行；A 先让 CI 变绿并关闭两个可直接利用的 P0，再修打包与发布；B 用共享循环和质量门禁锁住回归；C 在稳定基线上加可选平台能力。
- 暂不引入：真实模型 eval、K8s 沙箱、远程插件市场、OAuth provider、Windows 沙箱。
- 不做的抽象/配置化：不为单一实现者引入通用 sandbox 框架；不做 event-sourcing 重写；不做 provider 插件协议；不为每个新能力加一套独立配置系统。
- B0 | literal ask | 一个包、39 个任务、阶段 A/B/C/D 全做；每个任务独立 boundary + verify。适合 zcode 顺序执行，覆盖用户列出的全部范围。
- B1 | minimal change | 只做阶段 A + B 的发布/安全/质量门禁，C 的平台能力留到后续包。交付更快，但不满足用户“OS 沙箱/会话树/结构化压缩/插件/provider 深度”的范围。
- B2 | recommended alternative | 一个包、阶段 A+B 先交付，C+D 作为同包后续阶段；发布不被平台能力阻塞。适合先拿发布/安全/质量门禁收益，但用户列出的平台范围会延后。

### 本轮明确不做的内容
- 不实现 kitagent 式的常驻 daemon、capability/lease、K8s 沙箱、exactly-once 承诺。
- 不重写 TUI 或 workbench 前端架构；只补审批交互、SSE、e2e 和打包。
- 不引入新的强制运行时依赖；可选能力必须走 extras + 惰性导入。
- 不改变 AGPL-3.0-only 许可证、默认 telemetry 行为或既有 CLI 公开语义（除本包明确列出的安全收紧）。
- 不在本包执行真实模型调用、PyPI 发布、GitHub 仓库设置；这些是 manual 任务。
- 不把 `.spec/` 下的历史流程产物删除；只在 sdist 中排除。

## 变更边界
### 本轮主要改动模块
- 见 tasks.md 的 boundary 记录

### 明确未触碰的区域
- 不实现 kitagent 式的常驻 daemon、capability/lease、K8s 沙箱、exactly-once 承诺。
- 不重写 TUI 或 workbench 前端架构；只补审批交互、SSE、e2e 和打包。
- 不引入新的强制运行时依赖；可选能力必须走 extras + 惰性导入。
- 不改变 AGPL-3.0-only 许可证、默认 telemetry 行为或既有 CLI 公开语义（除本包明确列出的安全收紧）。
- 不在本包执行真实模型调用、PyPI 发布、GitHub 仓库设置；这些是 manual 任务。
- 不把 `.spec/` 下的历史流程产物删除；只在 sdist 中排除。

## 验证证据
### 备注
- 脚本验证：`uv run ruff check . && uv run ruff format --check .` -> exit 0 (runs:task-0001)
- 脚本验证：`uv run pytest -q tests/test_slash_commands.py tests/test_slash_injection_security.py` -> exit 0 (runs:task-0002)
- 脚本验证：`uv run pytest -q tests/test_mcp.py tests/test_mcp_filter.py tests/test_mcp_security.py` -> exit 0 (runs:task-0003)
- 脚本验证：`uv run python scripts/check_release_artifacts.py && uv run pytest -q tests/test_workbench_static.py` -> exit 0 (runs:task-0004)
- 脚本验证：`uv run pytest -q tests/test_release_workflow.py` -> exit 0 (runs:task-0005)
- 脚本验证：`uv run pytest -q tests/test_docs_contract.py` -> exit 0 (runs:task-0007)
- 脚本验证：`uv run pytest -q tests/test_loop_guard_parity.py tests/test_agent_turns.py` -> exit 0 (runs:task-0010)
- 脚本验证：`uv run pytest -q tests/test_agent_overflow_parity.py tests/test_agent_turns.py` -> exit 0 (runs:task-0011)
- 脚本验证：`uv run pytest -q tests/test_agent_budgets.py tests/test_plan.py tests/test_multi_agent.py` -> exit 0 (runs:task-0012)
- 脚本验证：`uv run pytest -q tests/test_plan_history.py tests/test_plan.py` -> exit 0 (runs:task-0013)
- 脚本验证：`uv run pytest -q tests/test_team_history.py tests/test_multi_agent.py` -> exit 0 (runs:task-0014)
- 脚本验证：`uv run pytest -q tests/test_plan_checkpoint.py` -> exit 0 (runs:task-0015)
- 脚本验证：`uv run pytest -q tests/test_audit_gating.py` -> exit 0 (runs:task-0016)
- 脚本验证：`uv run pytest -q tests/test_mcp_server_protocol.py` -> exit 0 (runs:task-0017)
- 脚本验证：`uv run pytest -q tests/test_permissions.py` -> exit 0 (runs:task-0018)
- 脚本验证：`uv run pytest -q tests/test_print_mode_commands.py` -> exit 0 (runs:task-0019)
- 脚本验证：`npm --prefix frontend run test:e2e` -> exit 0 (runs:task-0020)
- 脚本验证：`npm --prefix frontend run test:e2e && uv run pytest -q tests/test_runtime_approvals.py tests/test_runtime_sse.py` -> exit 0 (runs:task-0021)
- 脚本验证：`uv run python scripts/check_coverage.py` -> exit 0 (runs:task-0022)
- 脚本验证：`uv run python scripts/check_mypy_ratchet.py` -> exit 0 (runs:task-0023)
- 脚本验证：`uv run pytest -q tests/test_pty_sigint.py tests/test_concurrent_recovery.py` -> exit 0 (runs:task-0024)
- 脚本验证：`uv run nexuscli eval run --all` -> exit 0 (runs:task-0025)
- 脚本验证：`uv run pytest -q tests/test_os_sandbox.py` -> exit 0 (runs:task-0026)
- 脚本验证：`uv run pytest -q tests/test_linux_sandbox.py` -> exit 0 (runs:task-0027)
- 脚本验证：`uv run pytest -q tests/test_session_tree.py` -> exit 0 (runs:task-0028)
- 脚本验证：`uv run pytest -q tests/test_structured_compaction.py` -> exit 0 (runs:task-0029)
- 脚本验证：`uv run pytest -q tests/test_plugin_packages.py` -> exit 0 (runs:task-0030)
- 脚本验证：`uv run pytest -q tests/test_google_provider.py` -> exit 0 (runs:task-0031)
- 脚本验证：`uv run pytest -q tests/test_local_provider.py` -> exit 0 (runs:task-0032)
- 脚本验证：`uv run --with markdown,pygments python site/build.py && uv run pytest -q tests/test_docs_contract.py` -> exit 0 (runs:task-0033)
- 脚本验证：`uv run pytest -q tests/test_plan_readonly.py tests/test_plan.py` -> exit 0 (runs:task-0034)
- 脚本验证：`uv run pytest -q tests/test_workbench_dist.py tests/test_runtime_static.py` -> exit 0 (runs:task-0035)
- 脚本验证：`uv run pytest -q tests/test_headless_status.py` -> exit 0 (runs:task-0036)
- 脚本验证：`uv run pytest -q tests/test_cost_unknown.py` -> exit 0 (runs:task-0037)
- 脚本验证：`uv run pytest -q tests/test_prompt_budget.py` -> exit 0 (runs:task-0038)
- 脚本验证：`uv run pytest -q tests/test_docs_contract.py` -> exit 0 (runs:task-0039)

## 验证范围
- 级别：package
- 机器验证率：36/36
- 规则：check 与 done 复用该范围；只有记录升级触发时才执行全项目检查

## 哲学生效证据
### 行为成效回填
- Development Record 行为门禁通过

### 一致性门禁回顾
- 验收面（checklist 或成功标准）已通过

## 失误方向统计
- DEFERENCE：0
- OVERREACH：1
  - spec.md ### 5.6 偏离日志 D3
- KNOWLEDGE：0
- PROCESS：4
  - spec.md ### 5.5 修正日志 round 1
  - spec.md ### 5.5 修正日志 round 2
  - spec.md ### 5.6 偏离日志 D1
  - spec.md ### 5.6 偏离日志 D2
- 只统计，不自动调整 Autonomy。

## Git 记录
- 日期时间：2026-11-08 13:42
- 范围：NexusCLI 发布链与执行安全加固
- 功能：0001 修复仓库 lint/format 阻断，恢复 CI 绿；0002 将 slash `!` 注入接入 ToolExecutor 审批、审计与环境清洗；0003 让 MCP 的 readOnlyHint 不再产生本地可信只读；0004 让 wheel 自带 workbench，sdist 排除 node_modules 与 .spec；0005 增加 tag 触发的 PyPI release workflow 与 0.2.1 版本；0007 README 安装路径、社区文件与 docs contract；0010 让 query 与 ReAct 共享 LoopGuard 与 max_turns 撞顶提示；0011 让 query 与 ReAct 共享 provider 超限重试与 F12 截断保护；0012 plan/team worker 的 max_turns 对齐 config；0013 plan 每个任务接收会话历史；0014 team 并发 worker 使用隔离 history；0015 plan worker 传入 journal/run_id，恢复不重复副作用；0016 deny/skip 分支的审计受 audit_log 控制；0017 MCP server 不再就地改写共享 config；0018 permission prefix 规则按词边界匹配；0019 `-p "/命令"` 消费 frontmatter 的 mode/allowed_tools；0020 把 Playwright e2e 接入 CI；0021 workbench 支持 approve_run/approve_auto、SSE 审批与可配 timeout；0022 coverage baseline ratchet；0023 mypy ratchet；0024 pty SIGINT、并发恢复测试与 testing-matrix 更新；0025 hermetic eval 进入 CI 门禁；0026 OS 沙箱抽象 + macOS seatbelt；0027 Linux bubblewrap/Landlock 后端；0028 会话树：parent/branch 元数据与 `/tree`；0029 结构化压缩与文件台账；0030 插件包格式：用户级、命名空间、工具组件；0031 Google Gemini 原生 provider；0032 Ollama/llama.cpp 本地 provider；0033 文档站搜索与英文入口；0034 修复 `--mode plan` 假完成；0035 workbench dist 从仓库/包根解析；0036 headless 被拦截时可机读；0037 未知模型价格显式 unknown；0038 固定 prompt/tool 开销可诊断；0039 明确 `--mode plan` 的只读语义
- 操作：提交 / 推送
- 成效：Development Record 行为门禁通过
- 提交：pending local commit
- 提交区间：5326ac96905a00e5eff72be1409fa9aaa625592d..e8f3b7495ccaf14d12f9e8023ca6780302202c3b
- 推送：not-run (push stage follows commit)

## AGENTS.md 增量提议
```diff
+ - 权限 allow/deny 前缀规则必须先按 shell 操作符（`;`/`&&`/`||`/管道/`$()`）拆段逐段匹配，整串前缀匹配会被复合命令击穿 deny>allow；——来源:2026-11-07_fix-nexuscli-release-hardening（评审轮）
+ - 新增守卫/审批门的拒绝文案必须同步 `agent/results.py` 的 `GATE_DENIAL_MARKERS`，否则 headless `-p --json` 对被拦截运行报 status:"ok"；——来源:2026-11-07_fix-nexuscli-release-hardening（收尾轮）
+ - runtime SSE 帧新增事件类型必须带递增 `seq`（frontend `state.ts` 的 applyEvent 丢弃无 seq 帧，approval_* 事件曾因此为死代码）；——来源:2026-11-07_fix-nexuscli-release-hardening（评审轮）
+ - GitHub Actions 的第三方 action 引用一律 commit SHA pin，`tests/test_release_workflow.py` 有全量扫描测试拦截浮动 tag；——来源:2026-11-07_fix-nexuscli-release-hardening（收尾轮）
+ - config 项/组件必须接线到生产调用路径才算交付（sandbox.backend、插件 tool 组件、team blocked 曾均为静默 no-op，接线点在 `bootstrap.build_tool_registry`/`tools/builtins.py`/`cli.py` team 分支）；——来源:2026-11-07_fix-nexuscli-release-hardening（收尾轮）
+ - pytest `--cov` 原始数据落点由 `COVERAGE_FILE` 决定、`--cov-report` 路径不控制它；uv `--with` 可能因索引刷新需联网，机器到 PyPI 不通时用 `UV_OFFLINE=1` 走缓存重放 verify；——来源:2026-11-07_fix-nexuscli-release-hardening（收尾轮）
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
