# nexusCLI ZCode 差距存量台账任务包 - 项目范围

## 1. 问题定义

- **项目目标**：把 2026-09-28 ZCode 差距分析遗留的存量台账（`.zcode/round2-brief.md` 第五节的 P1 八项、P2 十四项，以及上一波 P0 评审留下的四个 note 与两个跟进项）全部落成可执行、可验收、可处置的任务，按波次实现并收尾归档。
- **目标用户**：nexusCLI 的开发者与使用者（BYOK 本地编码 CLI 用户）。
- **核心价值**：ZCode 对标差距从「台账记录」变为「已实现 + 有测试 + 有文档」的产品能力；每个台账条目要么交付、要么带证据处置，无一悬空。

## 2. 假设与待确认

### 2.1 已确认事实

- 基线（2026-09-29 本机实测）：`uv run python -m pytest` → 338 passed；`uv run ruff check .` → All checks passed!；`uv run ruff format --check .` → 110 files already formatted。
- 当前分支 `spec/add-zcode-backlog`（基于 main=853fe83，上一波 P0 已提交：后台 bash、只读直通、max_turns、/init、文档）。
- A1 缺口属实：`src/nexuscli/tools/background.py:107-121` 的 stop() 有 terminate→kill 升级阶梯，但 `tests/test_background.py` 仅覆盖 terminate 生效路径（`test_task_stop_terminates_process_and_freezes_output`，tests/test_background.py:189），kill 分支无测试。
- A3 缺口属实：`src/nexuscli/agent/subagent.py:183` 构造子代理 Agent 时未显式传 `max_turns`，默认值 20→200 后子代理回合成本随之放大 10 倍。
- A4 已有覆盖：非数值 `max_turns` 回退 200 由 `src/nexuscli/config.py:435-449` 实现，`tests/test_agent_turns.py:185` 已断言 `"abc"→200`。
- A2 已有承载：smoke 验证方式声明已写入基线提交 853fe83 的提交说明（scripted ToolExecutor smoke + 「no real TTY available in CI-style runs」说明）。
- hooks 现有 5 个事件（`src/nexuscli/config.py:104-110` HOOK_EVENT_FIELDS），缺 PermissionRequest / PostToolUseFailure。
- `/skill` 现为纯展示命令（`src/nexuscli/entrypoints/repl.py:865-883`），未强制注入模型上下文。
- `/usage` 现仅显示当次回合（`src/nexuscli/entrypoints/repl.py:622-624`），无历史统计。
- 仓库已有 SQLite 使用先例（`src/nexuscli/rag/code_index.py`、`src/nexuscli/runtime/tasks.py`，均参数绑定）与可复用的后台任务基建（`src/nexuscli/tools/background.py`）。

### 2.2 关键假设

- 台账规模估计（S/M/L）沿用 `.zcode/round2-brief.md` 第五节，本包按此排波。
- 凭据依赖类任务（MCP OAuth、原生 Anthropic 协议、OpenTelemetry）没有真实凭据环境，协议层一律以 mock 测试验收（任务 verify 已写明 mock 方式），真实端到端联调由用户后续自行执行。
- pypdf 与 opentelemetry SDK 加入 dev 依赖组后，CI 的 `uv sync --extra dev --frozen` 可复现（本机 uv 可用，已实测 uv run 正常）。

### 2.3 待确认问题

- ~~P2 是否全量交付~~ → 已关闭（本轮按全量 14 项交付完毕，见 tasks.md W2-W6 各 evidence 行）。
- ~~子代理 mailbox 工具是否对主代理开放~~ → 已关闭（仅子代理注册表注册，主代理不注册，tests/test_subagent_mailbox.py 白名单回归线钉住该口径）。

### 2.4 可选解释与取舍

- 当前选择：README.md 与既有 docs/*.md 的同步统一收口到收尾波 task-final-docs-sync -> 理由：约 15 个任务都要碰 README.md，若各任务自带文档同步，同波共写冲突无法拆解；收口后实现任务的 boundary 只含代码、测试与新建文件。
- 当前选择：P2「/expert 专家工作流」并入 task-workflow-engine -> 理由：与 C8 动态工作流共用同一 JSON 步骤图引擎与入口层（slash_commands/repl），拆开反而同波共写。
- 当前选择：A4 取 accepted_risk 不加 README 任务 -> 理由：回退行为已有测试钉住（tests/test_agent_turns.py:185）且为 config.py docstring 写明的有意设计，剩余风险仅是用户笔误时静默用默认值，可接受；不为它单开任务。
- 当前选择：W6 四个任务串行（depends-on 链）-> 理由：四者都改 repl.py（REPL 入口命令集中），串行链成本低于再拆两波的管理开销；波内其余文件互不重叠。
- 当前选择：trace-id 放 W2 而非 P2 收尾波 -> 理由：冲突聚类——它写 openai_compatible.py 与 executor.py，与 W5 的 otel/io-dump 共写 openai_compatible.py，放 W2 使 W5 只剩一条两任务串行链。

## 3. 功能范围

### 3.1 核心功能（MVP）

P1 八项 + 评审 note 修复 + 跟进项（详见 tasks.md W1–W4、W6、W7）：

- [x] A1：background stop terminate→kill 升级阶梯补测试
- [x] A3：子代理显式小 max_turns 上限
- [x] C1：hooks 补 PermissionRequest / PostToolUseFailure 事件
- [x] C2：工作区 hooks sha256 信任机制
- [x] C3：微压缩 microcompact
- [x] C4：/skill 强制加载
- [x] C5：本地 usage 历史统计（SQLite usage.db + /usage stats）
- [x] C6：PDF 文档读取（pypdf 可选依赖）
- [x] C7：插件系统·本地目录 MVP
- [x] C8：动态工作流·JSON 步骤图最小切片（含 /expert 入口）
- [x] B1：Mimosa 完整安全审计复跑（主会话执行）
- [x] 文档统一同步与全量回归归档

### 3.2 扩展功能

P2 十四项（除 /expert 并入 C8 外共 13 个任务，W2–W6 分载）：后台子代理与完成通知、子代理持久记忆目录、子代理间通信原语、/fork 会话分叉、MCP OAuth、OpenTelemetry 遥测、模型 IO 调试落盘、原生 Anthropic 协议、/effort 推理力度切换、会话长程目标 Goal、Cron 定时任务、traceId 全链路、嵌入式 rg 搜索加速。

### 3.3 不在范围内

- `.zcode/round2-brief.md` 第五节 nonPortable 表的 15 项（生成式命令注册表、沙箱契约、node_repl、TUI 侧栏、官方插件市场、Browser Use、Off-Peak、Coding Plan 登录、桌面/Web 协议、i18n、环境清洗、SEA、CUA、LSP、MCP 连接池）——不移植理由见该表原文。
- 同节「另补三项显式 triage」：AskUserQuestion、ReadSessionContext、bash cwd 持久化——归档理由见该表（bash cwd 属 C1 同域小项，后续与后台基建一并考虑）。
- B2 REPL 真机 smoke（external_blocked，owner=用户，需真实 TTY）。
- TS 编译器等价物、常驻 cron 守护进程、并行工作流执行器（C8 只做串行拓扑序最小切片）。

## 4. 最小实现路径

- 最简单可行方案：按「从轻到重」七个波次推进——W1 五个小任务（A 类 + S 级 P1）→ W2 M 级 P1 与轻量 P2 → W3/W4 两个 L 级 P1 及其关联 P2 → W5/W6 其余 P2 → W7 收尾（文档同步、Mimosa 审计、证据归档）；每波由一个实现工作流顺序完成，波末跑三条基线命令验收后进下一波。
- 暂不引入：并行工作流执行器、跨会话后台任务注册表、常驻调度器、插件市场/远程插件、per-agent 模型路由（frontmatter model 字段保持 parsed-but-ignored）。
- 不做的抽象/配置化：不为 microcompact/trace-id/邮箱做可插拔接口；telemetry 与 debug_dump 均为单一布尔开关，默认关闭。

## 5. 技术决策

- 技术栈：Python 3.11+ / uv / pytest / ruff（规则集 E/F/I/UP/B/SIM，pyproject.toml）；SQLite 均参数绑定；可选依赖走 extras 组。
- 本轮允许改动：`src/nexuscli/`、`tests/`、`docs/`、`README.md`、`TUTORIAL.md`、`pyproject.toml`、`uv.lock`、`.spec/`（以各任务 boundary 为准，boundary 未列出的文件不改）。
- 本轮不应触碰：main 分支保护语义；六项平台能力测试语义（tests/test_permissions.py、test_hooks.py、test_subagent.py、test_slash_commands.py、test_context.py、test_plan_mode.py + test_permission_mode.py 全绿且语义不回退）；web_fetch 既有 SSRF 防护；与本包任务无关的模块（web/、image/、lsp/、snapshot/、prompt/、types.py 等）。
- Git integration branch：`spec/add-zcode-backlog`（规划前已由编排方创建，本包沿用；一包一分支）。

### 5.4 编排策略

- route: build
- 编排形态：多轮动态工作流自迭代——GLM-5.3 负责规划与验收（主会话 + 规划工作流），GLM-5.3-Flash 负责实现（每波一个实现工作流），轮间由主会话验收衔接；Mimosa 完整审计与 /spec:done 归档提交由主会话在收尾执行。
- 路由依据：`route_decision.py --text "<包目标>"` 原始输出为 explore/score 0（五路由全零的并列弱信号，lanes=认证/安全、安全评审，risks=认证/信任边界）；主会话按 orchestration.md 判断本包为「具体实现 + 干净 ownership 切片」的 build 形态，以 `--route build` 复算（score=1，risks 保持认证/信任边界——对应 MCP OAuth 与 hooks 信任两任务，由收尾波 Mimosa 审计作为安全评审 lane 收口）。
- ownership: 同波任务两两不共写同一文件（逐对检查过，见 tasks.md 各波注记）；共写热点 repl.py / config.py / openai_compatible.py / subagent.py / slash_commands.py 的任务跨波排布并以 depends-on 串行；每波实现工作流按 assignment contract 五字段领取该波任务。
- waiting strategy: 实现工作流完成一波后返回，主会话运行该波全部任务的 verify 命令与三条基线命令，全部通过才派发下一波；任何 verify 失败回写任务清单并在下一轮修复，不静默跳过。
- verification gate: 每任务 verify 必须真实运行后才可勾选；波末三条基线命令（pytest / ruff check / ruff format --check）不劣化；收尾波 Mimosa 完整审计（depth=deep）无 blocker 级发现 + `check_spec_package.py` 无未通过门禁后，主会话执行 /spec:done 归档提交。

## 6. 成功标准与验证方式

- 台账全覆盖 -> verify: A 类 4 项、B 类 2 项、C 类 8 项、P2 14 项在本包中逐条映射为任务 id 或五枚举处置（见 tasks.md 与规划返回值 dispositions），无一悬空。
- 每任务可执行可验收 -> verify: tasks.md 中 26 个任务均带稳定 id、boundary（具体到路径）、verify（`uv run python -m pytest <文件>` / `uv run ruff` / 明确的手续型验证）与必要 depends-on；勾选前 verify 必须真实运行。
- 波次无共写冲突 -> verify: 每波内任务 boundary 的写集两两相交为空（tasks.md 各波注记列明检查结论）；冲突处已拆波或加 depends-on 串行。
- 基线不劣化 -> verify: 收尾时 `uv run python -m pytest` ≥ 338+新增 passed、`uv run ruff check .` 全绿、`uv run ruff format --check .` 全绿，尾行记入 checklist 验收证据。
- 安全收口 -> verify: 主会话 Mimosa security_scan（depth=deep）扫描完成且无 blocker 级发现；全部 SQL 参数绑定；源码/测试/文档零可用凭据字面量。
- 文档与实现一致 -> verify: README/docs 的命令表、工具表、配置项、事件表与实际 `/help`、`/tools`、config 字段逐项核对；锚点与内链逐点 grep 核验 0 断链。

## 7. 风险与约束

- 风险点：W6 四任务串行链拉长收尾周期 -> 缓解：四任务改动均小（REPL 分支 + 一个小模块），串行总成本可控；必要时可把链中无 repl 依赖的部分前移。
- 风险点：pypdf/opentelemetry 依赖引入破坏 CI `--frozen` -> 缓解：任务 verify 明确包含 `uv sync --extra dev --frozen` 退出码 0。
- 风险点：凭据类任务（OAuth/Anthropic）误写真实凭据 -> 缓解：凭据只从环境变量/密钥文件读取，测试全 mock，示例一律占位符（Mimosa 生成前约束）。
- 风险点：executor.py 多轮被改（W1 hooks 事件、W2 trace-id、W5 otel 埋点）引入回归 -> 缓解：三者已跨波串行，且每步 verify 都带 tests/test_permissions.py 或对应回归文件。
- 约束：全局遵守 `.zcode/round2-brief.md` 第四节（基线不劣化、六项平台能力不回退、Mimosa 安全约束、文档/注释风格、提交纪律）；本包规划阶段不做任何 git 操作，归档提交由主会话在收尾波执行。
