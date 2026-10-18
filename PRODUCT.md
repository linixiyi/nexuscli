# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

主要用户是开发者本人（repo 作者）：在自己和开源使用者的真实项目里跑 coding agent 任务——读写代码、执行命令、检索、多代理协作。使用场景是长时间、高频的终端会话，信息密度与耐看度优先。次要用户是 clone 仓库体验的开源读者（README 与文档站点是他们的第一印象）。

*（2026-10-18 第三轮 init：用户确认「主要视觉表面 = 终端 TUI 观感」，并确认 Platform 记为 `web` —— 本仓库能被设计工具链直接打开的视觉表面是 `site/` 静态文档站点，终端 TUI 以渲染约束的形式记在 Operating Context。用户定位与使用场景沿用前两轮结论文本。）*

## Product Purpose

NexusCLI 是运行在终端里的 AI Agent CLI：ReAct / Plan / Multi-Agent 三种模式，MCP、skills、记忆、快照、代码索引等内置工具，成功 = 用户信任它在一个会话里连续完成真实开发任务。

## Positioning

单仓库、Python 实现的完整 agent CLI（非空壳 demo）：核心路径有测试覆盖（72 个 `tests/test_*.py` / 705 个测试函数，CI 在 Linux 3.11+3.12 与 Windows 3.12 跑 ruff + pytest），功能面（skills/MCP/cron/workflow/SDK/Runtime API）显著大于同类 minimal agent。

## Operating Context

- **终端 TUI 是本产品的主视觉表面**（用户 2026-10-18 确认）。渲染层 Rich + prompt-toolkit；不换语言、不换栈（已确认方向：按 pi agent 风格重做观感）。
- **前景色单色系约束**：`src/` 全目录已无任何 `bg:` 背景色声明，界面完全复用用户终端调色板，浅色与深色终端都必须可读。这是当前实现的既有事实，也是后续改动不可回退的约束。调色板一律用 ANSI 语义色（`ansicyan` / `ansimagenta` / `ansibrightblack` 等），不用十六进制字面量——字面量在白色终端上实测只有 1.9–2.6:1，远低于 4.5:1 底线；ANSI 槽位由终端自己保证对其背景可读。
- 交互入口 `nexuscli`（REPL）与 `nexuscli -p`（单次模式）；31 个 slash 命令（`repl.py` 的 `SLASH_COMMANDS`）。
- 底部状态栏常驻：模型名 · effort 档位 · 上下文占用条与百分比 · cwd · 权限模式（`repl.py` 的 `_bottom_toolbar`）。
- 启动头是 pi 式两行：`NexusCLI v{version} ● api key ok|missing` + 一行 dim 提示（`/help commands · Shift+Tab permission mode`）。提示行只列真实绑定的交互；2026-10-18 polish 移除了从未绑定的 `? for shortcuts`。
- `/effort` 七档 `off|minimal|low|medium|high|xhigh|max`，无参数时循环切换，档位显示在状态栏模型名之后。
- 工具面：主 agent 路径 27 个工具 = 25 个内置（`get_builtin_tools()`）+ 2 个只读快照视图（`vfs_ls` / `vfs_read`），启用 MCP 时再并入远端工具（`bootstrap.py`）。子代理、SDK、`mcp serve` 路径只拿到 25 个，不含 vfs 视图。
- 文档站点 `site/`：由 `docs/*.md` + `TUTORIAL.md` 经 `site/build.py` 生成的静态 HTML（12 篇文档 + index + tutorial，产物入库），GitHub Pages 发布。

## Capabilities and Constraints

- 事件流驱动渲染：`text_delta / thinking_delta / tool_call / tool_result / usage / turn_complete / done / error / warning` 等事件（见 `src/nexuscli/agent/`）。
- **渲染字符串是测试契约**：`tests/test_render.py`、`tests/test_repl.py`、`tests/test_effort_switch.py`、`tests/test_usage_history.py` 断言具体输出；改观感必须同步更新断言。
- `src/nexuscli/render/plain.py` 是管道/plain 模式渲染器，输出保持无 ANSI。
- 会话转录、usage 台账、审批提示（approval callback）、HITL / policy / audit、hooks 信任门禁等功能行为不可变，只改观感。
- 内置 skill 仓库只带 1 个（`builtin_skills/web-access`）；`SkillRegistry` 另扫描 `~/.nexuscli/skills`、`~/.agents/skills`、项目的 `.nexuscli/skills` 与 `.agents/skills`，所以本机可见 skill 数量（本轮 41）属于环境状态，不是仓库事实，不要写进对外文案。
- TUI 无法用浏览器截图流水线验证；视觉回归靠测试里的字符串断言与真实终端跑。

## Brand Commitments

- 名称 **NexusCLI** 保留。
- `NEXUS` ASCII 块字形 logo **已不在代码中**（HEAD 版本 `rich_renderer.py` 里的 `_NEXUS_LOGO` 已随 pi 化重写移除），启动头只保留 `NexusCLI` 文字标识。
- 文档站点保留 `N` 字形标识：`site/index.html` 的 data-URI SVG favicon（紫色 `#7c5cff` 圆角方块 + 白色 N）与导航栏 `brand-logo`。
- 语言：界面文案英文为主，README / 文档 / 站点为中文。

## Evidence on Hand

- `README.md`（55K，完整功能文档）、`TUTORIAL.md`、`docs/`（12 篇主题文档）、`site/`（生成后的文档站点，与源码同步）。
- `src/nexuscli/render/rich_renderer.py` 与 `src/nexuscli/entrypoints/repl.py`：incumbent 视觉实现，当前为 pi 式前景色单色系（无 Panel、无 box、无 Table、无背景色）。
- 文档站点视觉现状：`site/style.css` 双主题（`data-theme="dark|light"`，CSS 变量），无外部资源依赖。
- 工作区存在未提交的 TUI 观感改动（`effort.py` / `model_selector.py` / `repl.py` / `rich_renderer.py` 及三个测试文件），是本轮 PRODUCT.md 事实核对的对象。

## Product Principles

1. 内容即界面——终端里最亮的东西永远是任务内容，不是装饰框。
2. 终端原生——颜色复用用户调色板，浅色/深色终端同样可读。
3. 密度与克制——工具调用单行、输出降级、状态常驻底部；不与用户抢屏幕。
4. 行为不回归——重设计只动观感层，事件流、审批、转录、usage 全部保持。

## Accessibility & Inclusion

正文与背景对比 ≥4.5:1；不硬编码纯黑/纯白背景，尊重终端主题色。当前实现以此为准（前景色单色系即该约束的落地方式）。
