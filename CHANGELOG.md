# Changelog

本文件记录 NexusCLI 的所有显著变更。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/),
版本号遵循 [SemVer](https://semver.org/lang/zh-CN/)。

## [0.2.1] - 2026-11-08

发布链与执行安全加固（spec 2026-11-07_fix-nexuscli-release-hardening）。

### Added

- **tag 触发的 PyPI release workflow**（`.github/workflows/release.yml`）：`push.tags: v*` 触发，Trusted Publishing（OIDC，`permissions.id-token: write` + `environment: pypi`），`uv build` 产出 wheel/sdist → `python -m twine check dist/*` → pinned 的 `pypa/gh-action-pypi-publish` 上传；仓库内不存放、不引用任何 API token。
- **workbench 静态产物随 wheel 发行**：hatchling `force-include` 把 `frontend/dist` 打到包内 `nexuscli/frontend/dist`；sdist 排除 `frontend/node_modules/**`、`frontend/test-results/**`、`.spec/**`；`scripts/check_release_artifacts.py` 在临时目录 `uv build` 后校验 wheel 含包内 `index.html`、sdist 无泄漏，失败打印缺失/多余路径。
- **Google Gemini 原生 provider**：`provider="google"` 走 Gemini 原生 HTTP（generateContent/streamGenerateContent），支持文本与图片输入、tool calls、usage；模型能力按 `ModelProfile` 字段判定，不按模型名硬编码。
- **会话树**：会话 JSONL 增加可选 `parent_id`、`branch`、`created_at`；`fork` 记录父会话与分支名；`/tree` 输出父子/分支树；旧会话无字段仍可读。
- Playwright e2e 接入 CI：Chromium 缺失时硬失败而非静默 skip，失败上传 Playwright 输出目录。
- **MCP HTTP 传输凭证门**：`nexuscli mcp serve --transport http` 必须配置 `NEXUSCLI_MCP_API_KEY`（未配置拒绝启动，缺/错 key 一律 401）；`tools/call` 改走 ToolExecutor 全链——审计日志、PreToolUse hooks、permission 规则与写域守卫此前被静默跳过，现已与其他入口一致（修复未授权本地调用 bash 的 P0）。
- 审计日志值级脱敏：Bash 命令串中的 `Bearer <token>` 与 JWT（eyJ 三段式）落盘前掩码，附防误伤反例测试。
- Runtime 任务队列恢复重试上限：租约恢复累计 5 次的任务转 `failed` 并留原因，毒任务不再无限占用 worker 槽位。
- `usage.db` 90 天保留窗口：`/usage stats` 时顺带 best-effort 清理窗口外旧行，库体积不随使用时长无界增长。
- `/turn-resume` 进入内置命令清单（/help 可见、Tab 可补全）。
- 工作台设计语言成文：根目录 `DESIGN.md`（impeccable design-schema）；浏览器表面工艺层（文本选区、输入 caret、表格数字 tabular-nums、链接下划线偏移）归入设计系统。

### Fixed

- **slash `!` 注入接入 ToolExecutor 全链**：`_run_injection` 不再直接调用 `CommandExecutor.execute`；项目级命令注入必须 `requires_approval=True`，无审批回调时 fail-closed；用户级命令自动执行但仍经审计记录与 `sanitize_environment` 环境清洗，原始 `os.environ` 不再传给子进程。
- **MCP `readOnlyHint` 不再产生本地可信只读**：构造 MCP Tool 时统一 `is_read_only=False`、`requires_approval=True`、`danger_level="medium"`；hint 仅用于展示/描述，不进入安全计算；plan 模式不再直接放行 MCP 工具。
- **MCP server 不再就地改写共享 config**：server 内部用 config 深拷贝设置 `hitl_mode="never"`，`load_config()` 返回的原对象不变。
- **permission prefix 规则按词边界匹配**：`bash(git diff:*)` 不再放行 `git difftool`，仍放行 `git diff` 与 `git diff --stat`。
- **query 与 ReAct 共享 LoopGuard 与 max_turns 撞顶提示**：`query()` 使用与 ReAct 相同的 `LoopGuard`，撞顶提示抽成共享 helper，两条路径输出同类 warning。
- 仓库 lint/format 阻断修复（`site/build.py` 两处 E501 与 format 重排），CI lint 恢复绿。

### Changed

- REPL `/plan` 与 `-p --mode plan` 同口径：per-run 配置副本打开 plan_mode 只读闸门，会话 config 不再被污染。
- `UserPromptSubmit` / `Stop` hooks 下沉至 plan/team 入口（`PlanExecuteAgent.run` / `AgentOrchestrator.run`），三条执行路径同效且单次触发。
- 写域守卫尊重 full-access：REPL Auto 模式（command guard 关闭）下域外写不再自相矛盾地拒绝；headless 无审批回调时仍 fail-closed。
- `nexuscli mcp serve --transport stdio` 在客户端关闭 stdin 时静默退出，不再栈崩溃。

### 发布

- 版本号 0.2.0 → 0.2.1（`pyproject.toml` 与 `src/nexuscli/__init__.py`）。

## [0.2.0] - 2026-10-19

上一版 0.1.0 以来的第一个发布:测试矩阵全面收口、真实运行暴露的缺陷修复、
TUI 一致性设计审计与文档/站点刷新。

### Added

- **测试矩阵 62 项收口**:17 个零验证(⬜)表面全部「自动化断言 + 真实运行」双补
  (/model、/task、/snapshot save+restore、/fork、/mcp、/memory、/index、/search、
  /hitl、/save、directory_tree、get_file_info、search_code、todo、team worker
  plan、SDK 等);45 个仅机器表面(🟡)中 44 个补真实运行(14.2 远程图片端到端因
  网关上游拒收远程图片如实标注,CLI 侧契约已由 wire dump 证实)。
  ——来源:2026-10-18_verify-zero-and-yellow(提交 5d0c261)
- **9 个仅真人(🟠)表面补自动化断言**:doctor、--version、/context、/tools、
  /policy、/usage、空 prompt Ctrl+C、Ctrl+D、bracketed paste。
  ——来源:2026-10-19_release-020-design-audit(本轮 Lane B)
- **MCP 会话复用回归测试**:同一 server 三次调用同一 pid、服务端计数 1→2→3
  (真实 stdio 子进程);缺必填参数在客户端本地拒发,不浪费网络往返。
  ——来源:提交 201aece
- **AGENTS.md(新)**:从归档 spec 包的「AGENTS.md 增量提议」与本包工程事实蒸馏的
  agent 工程守则——pytest HOME/USERPROFILE 隔离、pty CPR 应答、进程组 killpg、
  审批链与原因行、写域 allow 语义、MCP 握手/会话复用、rich.Markdown 硬换行、
  严格交替网关的摘要角色、能力驱动 vision。
  ——来源:2026-10-19_release-020-design-audit(本轮 Lane A)

### Fixed

- **真实运行暴露的 13 项缺陷全修**:plan worker 可写、压缩摘要角色违反严格交替
  (GLM 网关 HTTP 400 code 11133)、/snapshot 无手动入口、写域守卫无视 allow 规则
  且误报 `/dev/null`、/mcp 不列 server、vision 走模型名门槛而非能力字段、planner
  覆盖用户 worker-mode、resume 摘要误导、模型视觉徽标缺失、审批提示无原因行、
  召回约束措辞弱等。
  ——来源:2026-10-18_verify-zero-and-yellow(提交 5d0c261,两轮工作流)
- **TUI 一致性设计审计**:边框 Table(/history /sessions /usage)按 pi 式无框方向
  裁决并改;/context /compact /audit 同步无框化(标题行 + 无框网格;全 src 已无
  Panel 与边框 box,`rich.Table` 仅存 Table.grid 与 box=None 无框形态);新表面
  (/mcp 列表、审批原因行、快照确认文案、模型视觉徽标)过风格审查;渲染字符串
  断言同步更新。——来源:2026-10-19_release-020-design-audit(本轮 Lane D)

### Changed

- **文档与事实一致**:PRODUCT.md 更新为实测数字(104 个 `tests/test_*.py` /
  全量收集 1060 个测试)并移除已失效的「工作区存在未提交的 TUI 观感改动」证据行;
  README.md 经旧数字(951/948/955/82 等)核查无需改动。
  ——来源:2026-10-19_release-020-design-audit(本轮 Lane A)
- **文档站点**:site/ 产物与 docs/ 重建同步(含最新测试矩阵);style.css 双主题
  对比度/a11y/响应式 audit 与 polish。——来源:2026-10-19_release-020-design-audit
  (本轮 Lane E 与 F1)
- **新增测试的平台依赖审查**(修或 skipif)与 CI 状态核查。
  ——来源:2026-10-19_release-020-design-audit(本轮 Lane C)

### 发布

- 版本号 0.1.0 → 0.2.0(pyproject.toml 与 `src/nexuscli/__init__.py`)。
- 测试规模:矩阵包交付时 948 → 1046(提交 5d0c261),本轮继续增补
  (MCP 会话复用 +2 等);当前全量收集 1060 个测试(104 个 `tests/test_*.py`);
  0.2.0 最终计数以发版全量 pytest 为准。
