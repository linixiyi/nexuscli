# NexusCLI

**运行在终端里的 AI Agent CLI，面向真实项目开发场景**

读写文件 · 搜索代码 · 执行命令 · 联网检索 · MCP 工具 · 记忆 · 快照 · Runtime API

![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![uv](https://img.shields.io/badge/uv-package%20manager-DE5FE9)
![License](https://img.shields.io/badge/License-AGPL_v3-blue?logo=gnu)
![Tests](https://github.com/linixiyi/nexuscli/actions/workflows/ci.yml/badge.svg)

[架构总览](#-架构总览) · [文档站点](https://linixiyi.github.io/nexuscli/) · [运行教程](TUTORIAL.md) · [快速开始](#-快速开始)

> 🖥️ **文档站点**：[linixiyi.github.io/nexuscli](https://linixiyi.github.io/nexuscli/) —— 带导航与侧边目录的文档中心，收录本仓库全部 21 篇深度文档与运行教程；还有[交互式架构图](https://linixiyi.github.io/nexuscli/architecture.html)（节点搜索、路径追踪、深浅色主题）。站点页面由 `docs/*.md` 与 `TUTORIAL.md` 经 `site/build.py` 构建生成，内容与源文件同步。

**TL;DR**：NexusCLI 不是一个空壳 Demo，而是按真实 CLI 产品来做——核心路径有测试覆盖，也经过本地 smoke 和真实终端运行验证。最近一轮外围真实 LLM 冒烟全部通过：team 模式多 worker 协作（真实 token 消耗与两个 worker 迹象）、`mcp serve --transport http` 的 tools/list 返回完整工具表、`serve --http` 的 Runtime API 带 key 200 / 错 key 401。`uv run nexuscli` 进交互模式，`uv run nexuscli -p "..."` 跑单次查询；能力速览看下面的[特性速览表](#-特性速览)，细节按专题看[深入文档索引](#-深入文档索引)。第一次部署请从 [TUTORIAL.md](TUTORIAL.md) 开始：从安装依赖、配置模型 API 到第一次跑通 Agent 任务的完整运行教程。

**文档分层说明**：本 README 只保留能力宣称、要点速查与如实边界；每个专题的完整口径都在 `docs/` 对应文档里，README 侧的「详见」链接一律带节级锚点。同步口径：先更新 docs/ 细节文档，再核对 README 对应摘要句与索引行。

## ✨ 特性速览

能力按「快速上手 → 核心概念 → 深入专题」分层：本表是速览，每行的深入文档承载完整口径。

| 领域 | 一句话 | 深入文档 |
|---|---|---|
| Agent 运行模式 | 交互式终端 Agent（Rich + prompt-toolkit，界面只写前景色、复用你的终端主题），ReAct 工具循环、thinking/usage 事件、循环调用检测（同参重复达阈值拦截换策略）；也支持脚本化单次 `-p` 模式 | — |
| Plan-and-Execute | Planner 生成 DAG 按依赖批次并行，图级 checkpoint 支持跨进程续跑（`/plan --resume`）与协作式中断 | [docs/plan-execute.md](docs/plan-execute.md) |
| Multi-Agent 协作 | Planner / Worker / Reviewer、依赖调度、并行 worker、review 重试，team worker 继承 plan checkpoint | — |
| 动态工作流 | `/workflow run` 按 JSON 步骤图 DAG 依赖批次并行派发子代理；`/expert` 是同引擎三步专家流程 | [docs/workflows.md](docs/workflows.md) |
| 子代理 | `task` 工具委派独立子代理（后台执行、完成播报、文件邮箱互通），内置 general-purpose / explore，支持自定义代理 | [docs/subagents.md](docs/subagents.md) |
| 模型与上下文 | OpenAI-compatible 流式客户端（DeepSeek/GLM/Kimi/StepFun/Anthropic 原生），80% 预算自动压缩 + microcompact + provider 超限重试；BYOK 模型选择器与 API 拉取 | [docs/config.md](docs/config.md) |
| 工具与扩展 | 文件/Shell/检索/联网/记忆内置工具（写文件自动 py_compile 诊断），MCP client+server，代码索引 BM25 检索 | [docs/tools.md](docs/tools.md) |
| Skill 系统 | builtin/user/project 分层 + Agent Skills 规范目录扫描、Top-K 匹配懒加载、`save_skill` 经 HITL 沉淀 | [docs/skills.md](docs/skills.md) |
| 斜杠命令与插件 | 自定义斜杠命令（frontmatter/占位符/输出注入）与 `.nexuscli/plugins` 本地插件并入 | [docs/slash-commands.md](docs/slash-commands.md) |
| 记忆与持久化 | 静态项目记忆 + SQLite 动态长期记忆（去重/TTL/多信号时态召回），会话 JSONL 自动持久化、`-c`/`--resume`/`/fork` 跨进程恢复 | [docs/sessions.md](docs/sessions.md) |
| 快照 | 按需快照与恢复，Agent 可经 `vfs_ls` / `vfs_read` 挂载只读视图做改前对比 | [docs/sessions.md](docs/sessions.md#快照) |
| 安全与治理 | HITL 人工确认（五键位 + run 级授权）、deny>ask>allow 权限规则、七个生命周期 Hooks、JSONL 审计日志、环境清洗护栏 | [docs/permissions.md](docs/permissions.md) |
| Runtime API | 有历史的 thread / turn / 事件日志 HTTP 接入，持久化后台任务队列（lease/heartbeat、原子抢占、项目隔离） | [docs/runtime-api.md](docs/runtime-api.md) |
| 可视化工作台 | 浏览器入口：会话回放、SSE 实时事件流、任务面板、usage 统计、Web 审批与发起 turn | [docs/workbench.md](docs/workbench.md) |
| 定时任务 | `nexuscli cron` 离线任务注册表，触发由外部调度器驱动 | [docs/cron.md](docs/cron.md) |
| 上下文压缩 | 80% 预算触发，先无损 microcompact 再有损摘要；provider 真实超限自动压缩重试一次 | [docs/memory-context.md](docs/memory-context.md) |
| 会话增强 | `/fork` 分叉、`/goal` 长程目标、`/effort` 推理力度、`/history` 跨会话检索、usage 双口径 | [docs/repl-commands.md](docs/repl-commands.md) |
| 评测回归 | 离线任务级 eval：脚本化假 LLM 驱动真实 Agent，七种断言（轨迹序列/子集/禁用工具/回合与调用数上限）对累计轨迹判定；支持多轮用户模拟（脚本化用户在 agent 产出后追问同一会话、轮数硬上限）与记忆召回评测（临时库 seed 记忆，目标条目进 recall 前 K、干扰条目不排前）；回归以 `regression` 形式暴露并按 short/medium/long 难度分档定位，turns/工具调用数成本上涨显式告警但不改判定，可当 CI 门禁；另有真模型轨道 `eval run --real`——真实 LLM 走正常配置链驱动只读 builtin 工具面（白名单外失败关闭拒绝并留审计），pass^k 稳定性、真实 token 成本（含 cache/reasoning 拆分）与 LLM-as-judge 评分写入独立的 last-real-report.json（夜跑口径，不做 CI 门禁） | [docs/eval.md](docs/eval.md) |
| 可观测性 | trace_id 贯穿审计与 LLM 请求头、可选 OTel span（含 `telemetry.otlp` OTLP 导出，Langfuse 等外部 trace 视图）、模型 IO 脱敏落盘、usage 落库 | [docs/observability.md](docs/observability.md) |
| 代码索引 | `/index` 建本地词法索引，`/search` 与 `search_code` 按纯 Python BM25 相关性排序 | [docs/tools.md](docs/tools.md#代码索引与检索) |
| 图片输入 | `@image:` 引用本地/远程图片，自动压缩缩放，按模型能力自动降级 | [docs/tools.md](docs/tools.md#图片输入) |

## 📚 目录

**快速上手**

- [✨ 特性速览](#-特性速览)
- [🧭 环境要求](#-环境要求)
- [🚀 快速开始](#-快速开始)

**核心概念**

- [🔧 配置](#-配置)
- [🔐 权限与会话模式](#-权限与会话模式)
- [💬 交互命令](#-交互命令)
- [🧰 内置工具](#-内置工具)
- [💾 会话与快照](#-会话与快照)
- [💰 模型与费用](#-模型与费用)
- [🔌 MCP](#-mcp)
- [📡 Runtime API](#-runtime-api)
- [🖥️ 可视化工作台](#-可视化工作台)
- [🐍 SDK](#-sdk)

**深入专题**

- [📖 深入文档索引](#-深入文档索引)

**开发**

- [🧪 开发](#-开发)
- [📄 License](#-license)

## 📐 架构总览

[![NexusCLI 架构图](docs/architecture.png)](https://linixiyi.github.io/nexuscli/architecture.html)

静态快照如上，[交互式版本](https://linixiyi.github.io/nexuscli/architecture.html) 支持节点搜索、路径追踪、引导式故事和深浅色主题（按 `?` 查看操作指南）。图表源文件是 [docs/architecture.json](docs/architecture.json)，交互 HTML 由 [Archify](https://github.com/tt-a1i/archify) 从该源文件生成。

## 🧭 环境要求

- Python 3.11 或更新版本
- [uv](https://docs.astral.sh/uv/)
- 可选：rg（ripgrep）——PATH 上装有 rg 时，内置 `grep` 工具对目录的搜索自动改由 rg 子进程加速并映射回同一结果格式；没有 rg 时自动回退纯 Python 扫描，功能不受影响
- 可选：Chrome DevTools MCP 需要 Node.js 20.19.0 LTS 或更新版本、npm/npx 和 Chrome
- 可选：读取 PDF 需要 `uv sync --extra pdf`（pypdf）
- 可选：构建工作台前端需要 Node.js 22 LTS 与 npm（直接使用已提交的 dist 不需要）

安装与依赖细节见 [TUTORIAL.md](TUTORIAL.md)。

## 🚀 快速开始

```bash
git clone https://github.com/linixiyi/nexuscli.git
cd nexuscli
uv sync --extra dev --frozen
uv run nexuscli --help
```

启动交互模式：

```bash
uv run nexuscli
```

单次查询：

```bash
uv run nexuscli -p "帮我总结这个项目"
```

选择运行模式并输出机器可读的 usage/cost：

```bash
uv run nexuscli --mode plan -p "先读取 README，再验证项目" --json
uv run nexuscli --mode team --worker-mode plan -p "并行审计核心模块" --json
```

`-p` 模式没有人工审批通道，默认按失败关闭：需要写文件、跑命令、`save_memory` 等落盘动作的调用会被拒绝（审计留痕），显式加 `--yes` 才放行；旗标在 `-p "prompt"` 之前或之后均可，没有 `--approve-all` 别名。`-p` 里也可以触发自定义斜杠命令（`uv run nexuscli -p "/命令名 参数"`）。完整口径见 [docs/permissions.md](docs/permissions.md#单次--p-模式下没有审批弹窗)。

```bash
uv run nexuscli -p "记住这个项目用 uv 管理依赖" --yes
```

检查当前环境：

```bash
uv run nexuscli doctor --cwd .
```

会话持久化与恢复（REPL 对话自动保存，可跨进程继续）：

```bash
uv run nexuscli sessions       # 列出当前项目的最近会话
uv run nexuscli -c             # 继续本工程最近一次 REPL 会话
uv run nexuscli --resume <id>  # 按会话 id 恢复
```

## 🔧 配置

配置优先级：内置默认 → `~/.nexuscli/config.json` → 项目 `.nexuscli/config.json` → 项目 `.env` → CLI 参数 → 进程环境变量。

可以像 Java 项目一样，把 provider Key 写进项目 `.env`：

```dotenv
NEXUSCLI_PROVIDER=deepseek
NEXUSCLI_MODEL=deepseek-v4-flash
DEEPSEEK_API_KEY=your_key_here
```

provider-specific API Key 支持 `DEEPSEEK_API_KEY` / `ZAI_API_KEY` / `GLM_API_KEY` / `STEP_API_KEY` / `KIMI_API_KEY` / `ANTHROPIC_API_KEY`，通用 `NEXUSCLI_API_KEY` 亦可；不想明文写配置时 `llm.api_key` 支持 `"!command"` 形式接系统密钥管理器。连接本地网关：`NEXUSCLI_PROVIDER=openai-compatible` + `NEXUSCLI_BASE_URL` + `NEXUSCLI_MODEL`。最小 config.json 示例：

```json
{
  "llm": { "provider": "deepseek", "model": "deepseek-v4-flash" },
  "features": { "auto_snapshot": false }
}
```

可选诊断配置 `telemetry.enabled`（含可选 `telemetry.otlp` OTLP 导出，Langfuse 等外部 trace 视图）与 `llm.debug_dump` 默认关闭；`llm.vision` 显式声明模型图片能力（缺省按模型名特征猜测）。

优先级链、Anthropic 原生边界、`!command` 语义（不经 shell 执行、失败降级为空 key）、`agent.max_turns` / `agent.loop_detection_threshold`、价格覆盖的完整口径见 [docs/config.md](docs/config.md)。

## 🔐 权限与会话模式

config.json 的 `permissions` 里声明 `allow` / `deny` / `ask` 三组规则，按 **deny > ask > allow** 在 HITL 之前评估，用户级与项目级同名列表拼接合并去重：

```json
{ "permissions": { "allow": ["read_file", "bash(git diff:*)"], "deny": ["bash(curl | sh)"] } }
```

交互模式下 `Shift+Tab` 在三档会话权限模式间循环：`Default`（启动时策略）→ `Auto (full access)`（免审批 + 关闭守卫）→ `plan (read-only)`（只读闸门，非只读工具硬拒）→ 回到 `Default`。HITL 审批弹窗有五个键位：

| 键 | 行为 |
|---|---|
| `y` | 放行一次 |
| `r` | run 级授权：本次 agent run 内同工具免再弹（首按记 `hitl`、后续放行记 `run-scope`） |
| `a` | 切 auto 模式 |
| `s` | 跳过 |
| `n` | 拒绝 |

直通与审批都写审计日志。

三条如实边界必须知道：**`-p` 单次模式没有审批弹窗**，需要审批的工具一律按失败关闭（见[快速开始](#-快速开始)）；`bash` 与后台任务派生会把清洗后的环境传给子进程（剔除密钥形态变量），**这是护栏不是沙箱**，MCP stdio server、hooks runner 与 slash 命令注入路径未清洗，OS 级沙箱挂账；`save_skill` 等沉淀类操作默认强制人工确认，模型不会静默改变后续行为。

Hooks 以 shell 命令挂在七个生命周期事件上，hook 进程 stdin 收 JSON 载荷，exit 2 阻断、stdout 输出 JSON 同样生效；项目级 hooks 经 sha256 指纹信任确认后才会生效。会话权限模式、审批与 hooks 的完整口径见 [docs/permissions.md](docs/permissions.md#会话权限模式default--auto--plan) 与 [docs/hooks.md](docs/hooks.md)。

## 💬 交互命令

进入 REPL 后可用的常用命令速查：

```text
/resume [index-or-id]        # 会话内切换历史（-c / --resume 跨进程恢复）
/history <query> [--all]     # 跨会话历史关键词检索
/compact [focus]             # 手动压缩会话历史，可指定保留重点
/context                     # 查看当前上下文占用
/init [notes]                # 检查工作区并生成/增量更新 AGENTS.md
/memory ...                  # 静态/动态记忆管理（search/stats/delete/clear）
/model [provider] <model-id> # 交互式模型选择器（BYOK / API 拉取）
/usage [stats [N天]]         # 本回合与历史累计 usage；近 N 天落库汇总
/hitl default|auto           # 审批模式切换
/policy / /audit [N]         # 策略查看与审计日志
/index [path] / /search <q>  # 本地代码索引与 BM25 检索
/plan <task>                 # Plan-and-Execute（--resume 续跑）
/team <task>                 # 多 Agent 协作
/task add|cancel|log         # 后台任务队列
/workflow run <file>.json    # JSON 步骤图 DAG 派发
/expert <topic>              # 内置三步专家流程
/skill list|load|on|off      # Skill 管理
/snapshot / /restore         # 快照与恢复
/turn-resume [id]            # 回合级恢复：列出候选或按 id 从检查点续跑
/fork [title]                # 分叉当前会话
/goal set|show|clear         # 会话长程目标
/effort <档位>               # 会话内切换推理力度
/mcp                         # MCP server 状态
/help / /exit / /clear       # 基础命令
```

完整命令表、`/model` 选择器交互、`/usage` 双口径、`/history`、`/fork`、`/goal`、`/effort` 的逐条详解见 [docs/repl-commands.md](docs/repl-commands.md#usage-双口径)。

## 🧰 内置工具

NexusCLI 内置了一组 Agent 可以调用的本地工具和联网工具，速查如下（扩展面见下方说明与 [docs/tools.md](docs/tools.md)）：

| 类别 | 工具 |
|---|---|
| 文件 | `read_file`（支持 `.pdf`）· `write_file` · `edit_file` · `list_dir` · `directory_tree` · `get_file_info` |
| 检索 | `glob` / `glob_files` · `grep` / `grep_code` · `search_code` |
| 执行 | `bash` / `execute_command` · `task_output` · `task_stop` |
| 网络 | `web_search` · `web_fetch` |
| 记忆 | `save_memory` · `search_memory` |
| Skill | `load_skill` · `save_skill` |
| 任务清单 | `todo_write` · `todo_read` |
| 子代理 | `task`（支持 `run_in_background`） |
| 子代理邮箱 | `mailbox_post` · `mailbox_read`（仅子代理注册表可见） |
| 会话 | `revert_turn` |
| 快照视图 | `vfs_ls` · `vfs_read`（只读快照挂载） |

在 prompt 里引用图片：

```text
分析这张截图 @image:./screenshots/page.png
看看这个图片 @image:https://example.com/image.png
```

写文件、执行命令、远程 MCP 写操作、恢复快照等危险动作会经过 policy、HITL 和 audit 处理；写 `.py` 文件后自动 py_compile 语法诊断，错误就地回传给模型；`bash` / `execute_command` 支持 `run_in_background` 后台执行（`task_output` 读取、`task_stop` 终止）。想扩展工具面时，Skill（`load_skill` / `save_skill`）与自定义斜杠命令是两条不写 Python 代码的路径。

`read_file` 的 pdf 提取、`grep` 的 rg 加速与语义差异、`vfs_ls` / `vfs_read` 只读视图边界、代码索引与 BM25 检索、联网工具 SSRF 防护、图片输入管线、任务清单的补充口径见 [docs/tools.md](docs/tools.md#read_file-的-pdf-支持)；子代理委派与邮箱见 [docs/subagents.md](docs/subagents.md)。

## 💾 会话与快照

REPL 每轮对话自动追加到 `~/.nexuscli/sessions/<id>.jsonl`（首行会话元信息，之后每行一条消息；只打开不对话不产生文件），`-c` / `--resume` 跨进程恢复、`/fork` 分叉、usage 台账随转录续算——每个完成的普通回合写一条 `type: usage` 记录，恢复时求和为历史基线，`/usage` 的「历史累计 + 本进程」双口径始终对应当前转录。快照按需创建（`features.auto_snapshot` 默认关闭，大工作区每回合两次全量复制代价高），`/snapshot` / `/restore` 始终可用，Agent 可经 `vfs_ls` / `vfs_read` 挂载只读视图做改前对比。

会话内常用：

```text
/resume             # 列出最近会话（/resume 2 按序号切换）
/fork [title]       # 分叉当前会话为新会话并切换过去
/snapshot           # 创建快照（/snapshot save [label]）
/restore 1          # 按序号或 id 恢复快照
/snapshot clean     # 清理快照
```

JSONL 布局、恢复命令面、usage 续算双口径、fork 语义与快照配置的完整口径见 [docs/sessions.md](docs/sessions.md#会话持久化)。

## 💰 模型与费用

默认 provider/model 是 `deepseek/deepseek-v4-flash`（内置 1M 上下文与截至 2026-07-17 的官方价格）。流式请求开启 `stream_options.include_usage`，解析 cache hit/miss 与 reasoning Token；`/usage` 查看双口径汇总，`/usage stats [N天]` 看本地落库的近 N 天汇总，单次 `--json` 获取完整 usage/cost。成本以供应商返回的实际 Token 为准，不能只用"代码行数"精确推算。

价格覆盖、本地网关计费、BYOK 与各 provider 的 API Key 配置见 [docs/config.md](docs/config.md#模型token-与费用)。

## 🔌 MCP

NexusCLI 可以连接 MCP server，并把远端工具动态注册为 `mcp__<server-name>__<tool-name>`；也可以把自身作为 MCP server 暴露给外部客户端：`uv run nexuscli mcp serve --transport stdio`，或带凭证门的 HTTP 传输 `NEXUSCLI_MCP_API_KEY=<key> uv run nexuscli mcp serve --transport http --port 3000`（未配置 key 拒绝启动，缺/错 key 一律 401；`tools/call` 与其他入口同走审计与守卫链）。

> ⚠️ **部署边界**：作为 MCP server 运行时，NexusCLI 会在协议层强制关闭人工审批（`hitl_mode = "never"`），这意味着接入该 server 的任何客户端都获得了**无需审批的完整工具能力（包括执行命令、读写文件）**。只把 server 暴露给可信客户端；HTTP 传输默认只绑定 localhost，不要手动开放到公网。

HTTP 类 MCP server 支持 OAuth 2.0 授权码 + PKCE（令牌落盘 `~/.nexuscli/mcp-oauth.json`，0600）；每个 server 可配 `allowed_tools` / `blocked_tools` 做工具级过滤（精确名或 fnmatch 通配，allowed 优先）。需要登录态、浏览器状态或 JS 渲染页面时，建议接入 Chrome DevTools MCP（`uv run nexuscli mcp init-chrome` 一键生成配置）。工具级过滤、OAuth 授权流程边界、`mcp list` 与 smoke 的完整说明见 [docs/mcp.md](docs/mcp.md)。

## 📡 Runtime API

内置轻量 Runtime API，适合外部系统接入线程、turn、事件和后台任务：`uv run nexuscli serve --http --port 8080` 启动（请求头带 `x-api-key`），也可以只启动队列消费者而不暴露 HTTP：`uv run nexuscli worker --workers 2 --cwd .`。核心端点：`POST /v1/threads`、`POST /v1/threads/{id}/turns`、`GET /v1/threads/{id}/events`、`POST /v1/tasks` + `GET /v1/tasks` / 取消。任务队列按项目目录隔离；worker 使用 SQLite 原子事务领取任务，并通过 lease/heartbeat 恢复崩溃任务。API 任务同样接入审批通道，语义与 `-p` 模式一致（需要审批的调用按失败关闭）。

完整端点与 curl 示例见 [docs/runtime-api.md](docs/runtime-api.md)。

## 🖥️ 可视化工作台

浏览器里的工作台：会话回放、实时事件流（SSE）、任务面板、usage 统计、Web 审批与发起 turn。

```bash
NEXUSCLI_RUNTIME_API_KEY=dev-key uv run nexuscli serve --workbench --port 8080
# 打开 http://127.0.0.1:8080/
```

`frontend/dist` 已随仓库提交、开箱即用；自行构建 `cd frontend && npm ci && npm run build`（Node.js 22 LTS + npm）。服务端零新增 Python 依赖。

> ⚠️ 部署边界：默认只绑定 127.0.0.1、无多用户认证，不要开放公网。

<!-- TODO(截图): 用真实运行的工作台截取一张提交为 docs/workbench.png，然后把本注释替换为图片引用。占位期间不放死链。 -->

详见 [docs/workbench.md](docs/workbench.md#部署边界与安全)。

## 🐍 SDK

```python
from nexuscli.sdk import create_default_engine

engine = create_default_engine(cwd=".")
result = engine.ask_complete("解释这个项目")
print(result.text)

plan_result = engine.plan_complete("先读取 README，再总结项目结构")
team_result = engine.team_complete("让多个 Agent 并行检查核心模块")
```

## 📖 深入文档索引

全部 21 篇深度文档按主题排列；`docs/architecture.{json,png,html}` 是架构图源文件与交互版静态资产，不在本表。docs 文档与 README 摘要同源同步：实现细节以 docs 为准，README 摘要句跟随核对。

| 专题 | 文档 | 一句话 |
|---|---|---|
| 配置参考 | [docs/config.md](docs/config.md) | 优先级链、provider key、价格覆盖与循环/回合上限 |
| 内置斜杠命令 | [docs/repl-commands.md](docs/repl-commands.md) | 完整命令表、/model 选择器、usage 双口径与会话增强 |
| 内置工具补充 | [docs/tools.md](docs/tools.md) | pdf/rg 差异、vfs 只读视图、BM25 检索与图片输入 |
| Skill 匹配与沉淀 | [docs/skills.md](docs/skills.md) | 分层加载、Top-K 匹配与 save_skill 沉淀 |
| 记忆与上下文压缩 | [docs/memory-context.md](docs/memory-context.md) | 记忆三层、多信号时态召回、压缩预算与 provider 超限重试 |
| Plan-and-Execute | [docs/plan-execute.md](docs/plan-execute.md) | 图级 checkpoint、续跑与协作式中断 |
| 会话与快照 | [docs/sessions.md](docs/sessions.md) | 持久化、恢复与 usage 台账续算、快照与回放 |
| 权限与会话模式 | [docs/permissions.md](docs/permissions.md) | allow/deny/ask、命令级判定、审批透传与会话权限模式 |
| Hooks | [docs/hooks.md](docs/hooks.md) | 七个生命周期事件的挂载方式与安全约束 |
| MCP | [docs/mcp.md](docs/mcp.md) | stdio / HTTP server 接入、工具过滤与自身暴露为 server |
| 子代理 | [docs/subagents.md](docs/subagents.md) | 后台委派、自定义代理定义与文件邮箱 |
| 动态工作流 | [docs/workflows.md](docs/workflows.md) | JSON 步骤图 DAG、依赖批次并行与内置专家流程 |
| Runtime API | [docs/runtime-api.md](docs/runtime-api.md) | 线程、turn、事件流与后台任务的 HTTP 接入面 |
| 可视化工作台 | [docs/workbench.md](docs/workbench.md) | 浏览器可视化工作台：会话回放、实时事件流、任务面板与用量统计 |
| 插件系统 | [docs/plugins.md](docs/plugins.md) | 本地目录插件的发现、manifest 与组件并入链 |
| 自定义斜杠命令 | [docs/slash-commands.md](docs/slash-commands.md) | markdown 提示词、frontmatter 与占位符注入 |
| 定时任务 | [docs/cron.md](docs/cron.md) | 离线任务注册表与外部调度器驱动方式 |
| 评测回归 | [docs/eval.md](docs/eval.md) | 脚本化场景、断言体系与回归 diff 门禁 |
| 可观测性 | [docs/observability.md](docs/observability.md) | trace_id、OTel 遥测与 OTLP 导出、模型 IO 落盘与 usage 统计 |
| 规范技能开发 | [docs/spec-skill.md](docs/spec-skill.md) | 规范驱动开发技能的核心概念与质量门禁 |
| 测试矩阵 | [docs/testing-matrix.md](docs/testing-matrix.md) | 用户表面的测试状态：测过什么、没测过什么 |

## 🧪 开发

安装开发依赖：

```bash
uv sync --extra dev --frozen
```

运行检查（与 CI 完全一致）：

```bash
uv run ruff check .
uv run ruff format --check .
uv run python -m pytest
cd frontend && npm ci && npm run build
```

### 构建

发布/安装产物时使用，CI 不执行：

```bash
uv build
```

常用 smoke：

```bash
uv run nexuscli --version
uv run nexuscli --help
uv run nexuscli doctor --cwd .
uv run nexuscli --plain -p hello
```

## 📄 License

AGPL-3.0-only. See [LICENSE](LICENSE). 第三方依赖沿用其自身许可（MIT/Apache/BSD 等，与 AGPLv3 单向兼容）；kitagent 移植代码的 MIT 出处声明随源码保留。
