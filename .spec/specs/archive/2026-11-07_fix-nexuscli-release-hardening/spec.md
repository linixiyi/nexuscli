# NexusCLI 发布链与执行安全加固 - 项目范围

<!-- spec-format: 2 -->

## 1. 问题定义

- **项目目标**：把 `docs/research/2026-11-07_nexuscli-deep-research.md` 中的改进项，固化成一份可被 zcode 中其他 AI 顺序执行、逐任务验证的 spec 包；执行者不需要重新调研，只需要按 `tasks.md` 的 boundary 和 verify 工作。
- **目标用户**：在 zcode 中执行本包的 AI 代理，以及负责合并、发布和验收的 NexusCLI 维护者。
- **核心价值**：每个改动都有明确边界、依赖顺序和机器可执行的验收命令；失败时能定位到单个任务，而不是“整包重来”。
- **Literal ask**: 「把这些要改的都写成包，我要在 zcode 里面用其他 ai来修。」（m00459）；「不要强调时间什么的，比如第一周第二周什么的」（m00534）。
- **Stated purpose**: 把调研结论转成可执行任务包，交给 zcode 中的其他 AI 修复。
- **Inferred outcome**: zcode 代理按阶段 A → B → C → D 执行后，NexusCLI 的 CI 恢复绿色，三个 P0 被关闭，wheel/sdist 可发布，执行路径与质量门禁统一，平台能力有可选实现；confidence: high; evidence: m00459 的范围列举 + `docs/research/2026-11-07_nexuscli-deep-research.md` 的 P0/P1/P2 清单。
- **Constraints and exclusions**: 一个 spec 包、一个分支；不写日历时间；不修改与本包无关的源码；每个任务必须有 boundary 与 verify；不能把“未执行的发布/截图/仓库设置”写成已完成；真实模型评测、PyPI 凭据、GitHub 管理员设置由 owner 手动完成。
- **Trade-off preference**: maintainability + security，其次 delivery speed；不为赶进度合并任务或跳过验证。

## 2. 假设与待确认

### 2.1 已确认事实

- 仓库当前版本 `0.2.0`（`pyproject.toml`、`src/nexuscli/__init__.py`）；AGPL-3.0-only；166 个提交、0 个 git tag；PyPI `nexuscli` JSON 返回 404（2026-11-07 实测）。
- 源码 136 个 `.py` / 33,338 行；测试 141 个 `tests/test_*.py`、收集 1,693 条；本地全量 `pytest -q` 为 1,690 passed / 3 skipped / 417.84s。
- CI 徽章为 `failing`；Actions 页 Run 48–63 连续 16 次失败，最新 Run 63 的失败注解是 “Ruff lint / Process completed with exit code 1”；本地 `ruff check .` 报 `site/build.py:29:70` 与 `:45:72` 两处 E501，`ruff format --check .` 也要求重排 `site/build.py`。
- `uv build` 产出的 wheel 为 468KB / 142 files，**不含** `frontend/dist`；sdist 为 29MB，含 3,863 个 `node_modules` 条目；`frontend/test-results/.last-run.json` 被 git 跟踪；`.spec` 有 601 个文件被跟踪。
- P0-1：项目级 slash 命令的 `!` 注入在 `src/nexuscli/entrypoints/slash_commands.py:213-235` 直接调用 `CommandExecutor.execute`，没有经过 `ToolExecutor` 的审批、审计、路径守卫；环境来自 `src/nexuscli/tools/commands.py:358-362` 的 `os.environ.copy()`。
- P0-2：`src/nexuscli/mcp/client.py:259` 把 MCP 的 `readOnlyHint` 当作本地可信只读；`:282-284` 据此设置 `is_read_only=True`、`requires_approval=False`，使 plan 模式与 auto 模式绕过审批。
- P0-3：ReAct 循环在 `src/nexuscli/agent/agent.py:284-632` 含 LoopGuard、provider 超限重试、F12 `max_tokens` 截断保护和 `max_turns` 撞顶提示；`src/nexuscli/agent/query.py:19-182` 只有 `ContextWindowManager.prepare`，plan/team worker 走 `query()`，缺少上述四道护栏。
- P1：`plan_execute.py:373-382` 每任务传 `history=[]` 且未传 `journal`/`run_id`；`orchestrator.py:180/192/487` 让并发 worker 共享并回写 `self.history`；`tools/executor.py:270-285` 的 deny/skip 审计未受 `features.audit_log` 控制；`mcp/server.py:86` 就地改写共享 config 的 `hitl_mode`；`policy/permission_rules.py:109` 的 `startswith(rule.spec[:-2])` 无词边界；`cli.py:285-295` 的 `-p "/命令"` 只做文本展开，不消费 frontmatter 的 `mode`/`allowed_tools`。
- mypy 用 1 个 override 忽略 30 个模块、15,289 行（占 33,338 行的 45.9%）；没有 coverage 配置或门禁；`docs/testing-matrix.md` 自述只覆盖到 2026-10-18；pty 测试只有 4 个文件；CI 的 frontend job 不跑 Playwright e2e。
- `runtime/api.py:1518-1522` 的 `_workbench_dist_root()` 只认 `NEXUSCLI_WORKBENCH_DIST` 或 `cwd/frontend/dist`，不认包内路径；因此只把 dist 塞进 wheel 仍会在安装后显示空态。
- README 384 行 / 28KB，只有 badges 与 `docs/architecture.png`，没有 demo GIF；`README.md:303` 留有 `TODO(截图)`；docs 21 篇，site 无搜索；无 CONTRIBUTING/SECURITY/issue 模板。
- 现有相关测试：`tests/test_slash_commands.py`、`tests/test_mcp.py`、`tests/test_mcp_filter.py`、`tests/test_agent_turns.py`、`tests/test_plan.py`、`tests/test_multi_agent.py`、`tests/test_plan_checkpoint.py`、`tests/test_permissions.py`、`tests/test_mcp_server_protocol.py`、`tests/test_runtime_approvals.py`、`tests/test_runtime_sse.py`、`tests/test_runtime_static.py`、`frontend/e2e/workbench.spec.ts`。
- 真实运行（2026-11-08，用户级 `~/.nexuscli/config.json`：`openai-compatible` / `cbcn/glm-5.3-flash` / `http://localhost:20128/v1`，key 已配置但未写入仓库）：`-p "2+2"` 返回正确，单轮 5,415 input / 12 output tokens，`--json` 的 `cost` 为 `{}`，`session_usage` 的 `cost_usd`/`cost_cny` 为 0.0（该模型不在内置价格表）。
- 真实运行：`-p` + `--yes` 在临时仓库把 `calc.py` 的 `a - b` 改成 `a + b`，`pytest` 1 passed；ReAct 路径可正常读写。
- 真实运行：`--mode team --worker-mode react --yes` 在临时仓库生成 `mathx.py` 与 `test_mathx.py`，3 个 step 全部 COMPLETED，`pytest` 11 passed；team 路径可正常读写。
- 真实运行：`--mode plan --yes` 在临时仓库规划出 4 个写文件/跑测试任务，3 个并行批次；所有 worker 都返回「当前处于 plan mode（只读模式），无法写文件」，最终摘要仍把 4 个任务标为 COMPLETED，目录里没有 `hello.py`/`test_hello.py`，进程 exit 0。根因线索：`src/nexuscli/entrypoints/cli.py:242-248` 在 `--mode plan` 时把 `config.policy.plan_mode` 置为 True，`src/nexuscli/tools/executor.py:171` 的 plan 只读闸会拒绝写工具；plan worker 的拒绝文本没有被状态机识别为 blocked。
- 真实运行：`serve --workbench --cwd /tmp/nexuscli-realrun/task3` 返回 200，但页面是「NexusCLI 工作台 UI 未构建」空态；同一命令 `--cwd` 指向仓库根目录时能返回带 `/assets/index-*.js` 的构建产物。根因线索：`src/nexuscli/runtime/api.py:1518-1522` 只从 `self.cwd/frontend/dist` 找 dist，`--cwd` 是任务工作目录，不等于仓库/包根。
- 真实运行：`-p` 无 `--yes` 请求写文件时，`write_file` 与 `bash` 都被 fail-closed 拒绝，但进程 exit 0；`--json` 只有 `text`/`usage`，没有 `status`/`blocked` 字段，自动化无法区分「完成」与「被审批拦截」。
- 真实运行：交互式 TUI 可启动，footer 显示 `1 AGENTS.md file · 0 MCP servers · 43 skills · 27 tools · cbcn/glm-5.3-flash · 0% · ~/nexuscli · Default`；`/quit` 正常退出，无崩溃。

### 2.2 关键假设

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

### 2.3 待确认问题

- [x] 采用一个包还是三个包？→ 结论：一个包，内部按阶段 A/B/C/D 分节；四段共享 CI、打包、agent loop 和 workbench 文件，拆包会互相抢分支与写域。
- [x] 包内是否使用日历时间？→ 结论：不使用；只用阶段和 depends-on 表达顺序。
- [x] PyPI trusted publisher 由谁配置、是否已有 `pypi` environment？→ 结论：由 owner 配置 `pypi` environment 与 trusted publisher；未配置前 task_0006 保持 manual，不由 AI 代填。
- [x] Linux 沙箱默认后端选 bubblewrap 还是 Landlock？→ 结论：探测顺序 bubblewrap → Landlock；两者都不可用时返回 typed `OperationUnsupported`，不静默退回 none。
- [x] 是否接受在 CI 中跑全量 hermetic eval（耗时增加）？→ 结论：接受；它是离线 fake LLM，不产生 API 费用，进入 CI 门禁（task_0025）。
- [x] 英文 README 是全文翻译还是“英文入口 + 链接中文文档”？→ 结论：英文入口 + quickstart，不做全文翻译；后续可增量补英文文档（task_0033）。

### 2.4 可选解释与取舍

- 当前选择：一个包、四阶段、39 个任务；每个任务只改一个可验证边界。→ 理由：zcode 执行者需要低歧义、可中断、可回滚的粒度；一个包避免跨分支合并冲突；阶段 D 来自真实运行反馈，追加在 A/B/C 之后，不改变原任务编号。
- 当前选择：headless 被审批拦截时保持默认 exit 0，新增 `--fail-on-blocked` 与 JSON `status`，而不是直接改默认退出码。→ 理由：既有脚本可能把 exit 0 当作「对话完成」；先提供可机读状态与显式开关，避免破坏兼容。
- 当前选择：未知模型价格返回 `null` + `cost_note`，不再伪造 0.0。→ 理由：0.0 会被成本报表当成真实零成本；未知应显式未知，并提示配置 `llm.prices`。
- 当前选择：`--mode plan` 保持只读，但 planner 不得把写任务排进计划；若 worker 仍被拦截，任务状态必须是 `blocked`，最终摘要与退出码不得报成功。→ 理由：保留只读保证，同时消除「假完成」。
- 当前选择：P0-1 的项目级注入默认要求审批，用户级注入可自动批准但仍走审计与环境清洗。→ 理由：项目目录来自被克隆的仓库，属于不可信输入；用户级命令是用户自己安装的，保留便利性。
- 当前选择：MCP 工具不再信任 `readOnlyHint`，统一 `is_read_only=False`、`requires_approval=True`。→ 理由：annotation 由 server 自报，不能作为本地安全边界；牺牲 plan 模式下 MCP 只读工具的便利性，换取不可绕过。
- 当前选择：wheel 内置 workbench dist，同时保留 `NEXUSCLI_WORKBENCH_DIST` 覆盖。→ 理由：`uv tool install` / `pip install` 后 workbench 必须开箱可用；外部覆盖仍用于开发。
- 当前选择：质量门禁用 baseline ratchet，而不是一次性要求 100% coverage / 0 mypy ignore。→ 理由：现有 45.9% 源码在 mypy ignore 下，一次性清零会阻塞发布；ratchet 保证只减不增。

### 2.5 已证伪路径

- 无（暂无证伪记录；本轮只写包，不执行修复。若执行中发现某任务的前提不成立，在执行分支的 `deviations.md` 记录方案、证据与处理，并回写本包）。

<!-- spec:external-facts -->
### 2.6 外部事实

- pi（earendil-works/pi）在 2026-11-07 约 120,600 stars，MIT，TypeScript monorepo；其定位是 minimal, extensible agent harness，刻意不做内置 sub-agents/plan/permissions，安全依赖 project trust 与容器化。
- kitagent（zed123214/kitagent）在 2026-11-07 约 64 stars，MIT，Python 3.12；其强项是常驻 Core、K8s 沙箱、Loop/LangGraph 双引擎与 durable 语义；其 README 的沙箱数字为自述，未在本包复跑。
- 以上外部事实只用于解释“为什么本包优先补发布链、执行安全和质量门禁”，不作为验收依据；详细对比见 `docs/research/2026-11-07_nexuscli-deep-research.md`。

## 3. 功能范围

### 3.1 核心功能（MVP）

- 发布链：修 `site/build.py` 的 lint/format；关闭 P0-1/P0-2；wheel 内置 workbench、sdist 排除 `node_modules`/`.spec`；tag 触发 PyPI release workflow；版本升到 0.2.1；README 安装路径、社区文件、demo 资产；main 必需检查。
- 执行路径：把 LoopGuard、provider 超限重试、F12 截断保护、`max_turns` 撞顶提示下沉到共享循环；修 plan history、team history 竞争、plan journal、deny/skip 审计、MCP server config、prefix 词边界、`-p` frontmatter；workbench 补 approve_run/approve_auto、SSE 审批、可配 timeout、Playwright e2e；coverage/mypy/pty/hermetic eval 门禁。
- 平台能力：可选 OS 沙箱（抽象 + macOS seatbelt + Linux bubblewrap/Landlock）；会话树；结构化压缩与文件台账；插件包格式（用户级/命名空间/工具）；Google Gemini 原生 provider；Ollama/llama.cpp 本地 provider；文档站搜索与英文入口。
- 真实运行反馈（阶段 D）：`--mode plan` 的空转任务不得标 COMPLETED；workbench dist 从仓库/包根解析而不是 `--cwd`；headless 被审批拦截时 `--json` 带 `status` 并支持 `--fail-on-blocked`；未知模型成本显示 `unknown`/`null` 而不是 0.0；固定 prompt/tool 开销可诊断；`--mode plan` 的帮助/文档明确「只读，不执行写入」。

### 3.2 扩展功能

- [ ] 真实模型 eval 进入定期任务（需要 API key 与预算，不在本包自动执行）。
- [ ] PyPI 发布后自动生成 GitHub Release notes 与 provenance attestation。
- [ ] Windows 沙箱后端（Job Object/AppContainer）与 CI Windows e2e。
- [ ] 远程插件 registry / 签名校验 / 市场。
- [ ] OAuth 订阅登录与更多 provider catalog。

### 3.3 不在范围内

- 不实现 kitagent 式的常驻 daemon、capability/lease、K8s 沙箱、exactly-once 承诺。
- 不重写 TUI 或 workbench 前端架构；只补审批交互、SSE、e2e 和打包。
- 不引入新的强制运行时依赖；可选能力必须走 extras + 惰性导入。
- 不改变 AGPL-3.0-only 许可证、默认 telemetry 行为或既有 CLI 公开语义（除本包明确列出的安全收紧）。
- 不在本包执行真实模型调用、PyPI 发布、GitHub 仓库设置；这些是 manual 任务。
- 不把 `.spec/` 下的历史流程产物删除；只在 sdist 中排除。

## 4. 最小实现路径

- 最简单可行方案：按阶段 A → B → C → D 顺序执行；A 先让 CI 变绿并关闭两个可直接利用的 P0，再修打包与发布；B 用共享循环和质量门禁锁住回归；C 在稳定基线上加可选平台能力。
- 暂不引入：真实模型 eval、K8s 沙箱、远程插件市场、OAuth provider、Windows 沙箱。
- 不做的抽象/配置化：不为单一实现者引入通用 sandbox 框架；不做 event-sourcing 重写；不做 provider 插件协议；不为每个新能力加一套独立配置系统。

### 4.1 方案阶梯

- B0 | literal ask | 一个包、39 个任务、阶段 A/B/C/D 全做；每个任务独立 boundary + verify。适合 zcode 顺序执行，覆盖用户列出的全部范围。
- B1 | minimal change | 只做阶段 A + B 的发布/安全/质量门禁，C 的平台能力留到后续包。交付更快，但不满足用户“OS 沙箱/会话树/结构化压缩/插件/provider 深度”的范围。
- B2 | recommended alternative | 一个包、阶段 A+B 先交付，C+D 作为同包后续阶段；发布不被平台能力阻塞。适合先拿发布/安全/质量门禁收益，但用户列出的平台范围会延后。

Recommendation: B0 because 用户明确要求把“要改的都写成包”，且 A/B/C/D 的依赖顺序强于并行收益。Flips if A2 is FALSIFIED（执行环境不能跑 uv/npm，机器 verify 不可用）或 owner 明确要求三个分支并行。

## 5. 技术决策

- Workflow: FULL
- 技术栈：Python 3.11+、uv、pytest、ruff、mypy、GitHub Actions、React 19 + Vite、Playwright；不新增强制依赖。
- 证据协议：evidence: runs（每个机器 verify 必须真实执行并记录退出码；manual 任务记录执行人、命令、结果）。
- 本轮允许改动：仅 `tasks.md` 中每个任务 boundary 列出的文件；跨任务共享文件（如 `ci.yml`、`agent.py`、`query.py`）按任务顺序改，后一个任务负责 rebase。
- 本轮不应触碰：AGPL 许可证、默认 telemetry 开关、无关模块、生成物（`frontend/dist` 由构建产生，不手工编辑）、任何密钥或真实用户数据。
- Autonomy: medium
- Git integration branch：`spec/2026-11-07_fix-nexuscli-release-hardening`

### 5.1 验证策略

- 范围级别：package
- UI surface: changed
- 变更对象：本任务包 boundary 涉及的模块、入口、数据契约和发布载荷；UI 变更集中在 workbench 审批交互与 README/site 文档。
- Browser check: `npm --prefix frontend run test:e2e`
- Browser check 适用任务：task_0020、task_0021；凡是改 workbench UI 的任务必须在 verify 中重复该命令。
- 快速检查：`uv run pytest -q <该任务新增/相关测试>`；`uv run ruff check .`；`uv run ruff format --check .`。
- 集成检查：`uv run pytest -q tests/test_plan.py tests/test_multi_agent.py tests/test_runtime_approvals.py tests/test_runtime_sse.py`。
- 全项目检查：适用外；仅在改动共享基础设施、跨模块契约、构建/依赖、发布载荷，或 package/integration 检查暴露跨边界回归时执行 `uv run pytest -q`（基线 1,690 passed / 3 skipped，约 7 分钟）、`uv run --with mypy mypy src/nexuscli` 与 `uv build`。
- 升级触发：只有改动共享基础设施、跨模块契约、构建/依赖、发布载荷，或 package/integration 检查暴露跨边界回归时，才把范围升级并记录理由。

<!-- spec:correction-log -->
### 5.5 修正日志

| round | approach | result | new information |
| --- | --- | --- | --- |
| 1 | 五道并行评审（CI/文档/前端/脚本/安全）+ 每条发现独立复核：确认 2 个 high（权限复合命令绕过、REPL 注入回归）与 10 个 medium（SSE 审批帧死代码、headless 漏报写域拒绝、发布链未接产物守卫、twine 浮动版本、ci.yml 无 permissions 等），就地修复 | 12/12 确认项修复成功，scoped 回归全绿（运行 dwfrun-cacfb0a4） | 新输出：prefix/deny 规则必须先按 shell 操作符拆段再逐段匹配；前端 SSE 帧必须带递增 seq 否则被 applyEvent 丢弃；新证据见 tasks.md 各任务 evidence 行与评审看板 |
| 2 | 收尾轮：3 条接线缺口（team 模式 blocked 识别、插件工具组件生产接线、sandbox.backend 生产接线）复核确认后按各自任务意图接线；7 条 low 备忘修复（Actions 全量 commit SHA pin 并新增扫描测试锁住）；终检门禁 5/5（全量 pytest+coverage、e2e、mypy 棘轮、ruff 双检） | 全量 pytest 0 failed、coverage 棘轮与 e2e、mypy 棘轮、ruff 双检全部 exit 0（运行 dwfrun-1b24d3de） | 新输出：新增守卫/审批门的拒绝文案必须同步 agent/results.py 的 GATE_DENIAL_MARKERS，否则 `-p --json` 对被拦截运行报 ok；新证据见收尾轮看板与各修复回归记录 |

<!-- spec:deviation-log -->
### 5.6 偏离日志

| id | literal ask | actual | reason | reversibility | authority |
| --- | --- | --- | --- | --- | --- |
| D1 | task_0022 boundary 限 scripts/check_coverage.py、coverage-baseline.json、ci.yml、pyproject | 授权 task_0022 实现者修复 tests/test_agent_turns.py 与 tests/test_workbench_static.py 共 6 个跨任务缝隙失败（task_0038 新增 prompt_profile 字段、task_0035 改 dist 解析顺序叠加 worktree 存在 frontend/dist 所致，阻塞全量套件绿） | 全量套件绿是 evidence: runs 门禁与包验收前提；只把测试断言更新到当前意图契约，不放宽、不改产品代码 | reversible | checkpoint（运行中升级 dwfq-523db66d-1 由主会话按用户本轮执行授权裁决，细则在 tasks.md 0022 evidence） |
| D2 | 评审/收尾轮修复按各任务 boundary 最小改动 | 跨任务接线缺口的最小修复落在生产调用点：M1 涉 orchestrator.py 与 agent/results.py（0036 boundary 外）、M2 涉 bootstrap.py（0030 boundary 外）、M3 涉 tools/builtins.py 等调用点（0026 boundary 外） | 接线缺口本身跨任务边界，不落生产调用点则用户路径不可达，任务意图落空 | reversible | checkpoint（收尾轮 workflow 经确认启动、修复范围含接线点授权，保持 fail-closed 默认不变） |
| D3 | 包范围含 0006 发布 PyPI、0008 demo GIF/截图、0009 main 分支保护三项 owner 手动任务 | 三项连文带 boundary/verify 逐字移出本包，转入后续包 2026-11-08_add-publish-demo-protection；本包收敛为 36 项机器任务 | 三项均需 owner 凭据/管理员权限/本地录制，AI 不可代填；用户初始指令即预定 manual 任务跳过并标注，本轮明确指令 done+push | reversible | owner grant（用户明确指令本轮 done+push；三项原文在新包中保留，可随时移回） |

## 6. 成功标准与验证方式

- [x] `ruff check .` 与 `ruff format --check .` 在仓库根目录通过，CI lint job 不再因 `site/build.py` 失败 -> verify: task-0001
- [x] 项目级 slash `!` 注入必须经过审批/审计/环境清洗，无审批回调时 fail-closed；用户级注入保留自动批准但仍有审计 -> verify: task-0002
- [x] MCP 工具的 `readOnlyHint` 不再产生本地可信只读；所有 MCP 工具 `requires_approval=True`、`is_read_only=False` -> verify: task-0003
- [x] wheel 内含 `nexuscli/frontend/dist/index.html`，安装后 workbench 非空态；sdist 不含 `node_modules` 与 `.spec` -> verify: task-0004
- [x] `v0.2.1` tag 触发 release workflow，构建并校验 wheel/sdist；版本号在 `pyproject.toml` 与 `__init__.py` 一致 -> verify: task-0005
（0006/0008/0009 三项 owner 手动标准已随任务逐字转入后续包 2026-11-08_add-publish-demo-protection，见 5.6 D3）
- [x] README 有 PyPI/uv/source 三种安装路径，社区文件齐全，docs contract 测试通过 -> verify: task-0007
- [x] `query()` 与 ReAct 共享 LoopGuard，`max_turns` 撞顶有可见提示 -> verify: task-0010
- [x] `query()` 与 ReAct 共享 provider 超限 compact/force_trim 重试与 F12 截断保护 -> verify: task-0011
- [x] plan/team worker 的 `max_turns` 从 config 读取，不再 hardcode 8/200 -> verify: task-0012
- [x] plan 每个任务收到会话历史，任务间不串线 -> verify: task-0013
- [x] team 并发 worker 使用隔离 history，合并顺序确定，无 last-writer-wins -> verify: task-0014
- [x] plan worker 传入 journal/run_id，恢复后不重复副作用 -> verify: task-0015
- [x] deny/skip 分支的审计受 `features.audit_log` 控制 -> verify: task-0016
- [x] MCP server 不再就地改写共享 config 的 `hitl_mode` -> verify: task-0017
- [x] `prefix:*` 规则按词边界匹配，`git diff:*` 不放行 `git difftool` -> verify: task-0018
- [x] `-p "/命令"` 消费 frontmatter 的 `mode`/`allowed_tools` -> verify: task-0019
- [x] Playwright e2e 在 CI frontend job 中可重复通过 -> verify: task-0020
- [x] workbench 支持 approve_run/approve_auto、SSE 审批事件、可配 timeout，并有 e2e 覆盖 -> verify: task-0021
- [x] coverage 有 baseline 与 ratchet，低于 baseline 时 CI 失败 -> verify: task-0022
- [x] mypy 有错误数 + ignore 模块/行数 ratchet，只减不增 -> verify: task-0023
- [x] pty SIGINT 与并发恢复有自动化测试，testing-matrix 更新到当前 HEAD -> verify: task-0024
- [x] hermetic eval 在 CI 中运行，任一场景失败即失败 -> verify: task-0025
- [x] OS 沙箱有可选抽象 + macOS seatbelt，默认关闭，请求不可用时 fail-closed -> verify: task-0026
- [x] Linux bubblewrap/Landlock 后端可探测、可测试，缺依赖时给出类型化错误 -> verify: task-0027
- [x] 会话树有 parent/branch 元数据与 `/tree` 查询，旧会话仍可读 -> verify: task-0028
- [x] 压缩摘要为结构化格式并维护文件台账，长会话可恢复关键上下文 -> verify: task-0029
- [x] 插件支持用户级目录、命名空间、工具组件，工具走同一 ToolExecutor 审批链 -> verify: task-0030
- [x] Google Gemini 原生 provider 可按统一接口构建并通过 fake server 测试 -> verify: task-0031
- [x] Ollama/llama.cpp 本地 provider 可配置并走 OpenAI-compatible 适配 -> verify: task-0032
- [x] 文档站有搜索索引，README 有英文入口，示例可运行 -> verify: task-0033
- [x] `--mode plan` 不再把只读空转任务标为 COMPLETED；写任务被拦截时任务状态为 blocked，最终摘要与退出码不报成功 -> verify: task-0034
- [x] `serve --workbench --cwd <仓库外目录>` 仍能解析仓库/包内 `frontend/dist` 并返回构建产物；空态页给出解析过的路径与修复指引 -> verify: task-0035
- [x] headless 被审批拦截时 `--json` 含 `status: blocked` 与原因；加 `--fail-on-blocked` 后退出码为 2 -> verify: task-0036
- [x] 未知模型价格返回 `cost: null` + `cost_note`，不再用 0.0 冒充真实成本 -> verify: task-0037
- [x] 简单问答的固定 prompt/tool 开销可诊断；默认与 lean 模式的估算 token 有测试，lean 模式减少非核心工具 schema -> verify: task-0038
- [x] `--mode plan` 的 CLI help/README/docs 明确「只读、不执行写入」，`--yes` 在该模式无效并有提示 -> verify: task-0039

## 7. 风险与约束

- 风险点：P0-1 改成走 ToolExecutor 后，项目级 slash 注入在 `-p` 模式会从“静默执行”变成“失败关闭”，可能让既有脚本报错。→ 缓解：在 `docs/slash-commands.md` 与错误消息中明确 `--yes`/审批路径；测试覆盖无回调、拒绝、批准三种分支。
- 风险点：MCP 工具统一不可信后，plan 模式不能再用 MCP 只读工具，auto 模式也会弹审批。→ 缓解：在 `docs/mcp.md` 说明安全取舍；如 owner 需要，后续加显式本地 `trusted_readonly`，但默认关闭。
- 风险点：wheel 内置 dist 的路径在 hatchling、editable install、`uv tool install` 三种模式下可能不一致。→ 缓解：`scripts/check_release_artifacts.py` 同时检查 wheel 内容和 `_workbench_dist_root()` 回落；`tests/test_workbench_static.py` 用临时 cwd 验证。
- 风险点：Playwright e2e 在 CI 中可能因浏览器安装、端口、超时抖动。→ 缓解：serial 运行、固定端口、`retries: 0`、失败上传 trace；只在 ubuntu 3.12 frontend job 跑。
- 风险点：coverage 全量插桩会让 7 分钟测试更慢，且可能触发时序敏感失败。→ 缓解：只在 ubuntu 3.11 跑；baseline 记录实测值；插桩失败与产品失败分开报告。
- 风险点：mypy ratchet 依赖 mypy 版本与 Python 版本；升级后错误数可能漂移。→ 缓解：在 dev extras 固定 mypy 版本；baseline 记录版本与 Python 版本。
- 风险点：OS 沙箱后端依赖宿主内核与工具，CI 无法覆盖所有平台。→ 缓解：抽象 + 探测 + skip；真实后端测试用 fake runner，真实探测只在对应平台跑。
- 风险点：会话树/结构化压缩会改变会话文件格式，旧会话可能读不了。→ 缓解：新增字段可选，读取时兼容旧格式；迁移测试覆盖旧 JSONL。
- 风险点：插件工具组件会扩大攻击面。→ 缓解：工具必须走同一 ToolExecutor，项目级插件默认需要审批；插件发现阶段不执行代码。
- 风险点：PyPI 发布需要 owner 凭据，AI 无法代替。→ 缓解：task_0006 明确 manual，workflow 只负责构建与上传，凭据由 trusted publisher 提供。
- 风险点：修完 lint 后可能暴露 mypy/pytest/frontend 的其他失败。→ 缓解：A9 标为 UNVERIFIED；task_0001 只修 lint，后续任务各自负责自己的门禁；若 CI 出现新失败，按失败 job 回写新任务，不把 lint 绿当成全绿。
