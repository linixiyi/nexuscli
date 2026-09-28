# 第四波 文档同步 — 简报

只改 `README.md`、`TUTORIAL.md`、`src/nexuscli/entrypoints/repl.py` 的 `/help` 输出文本（SLASH_COMMANDS 列表加 `/compact`——若集成车道已加则跳过此项）。

## README.md
- 在功能介绍区新增（或并入最贴近的既有章节）六个小节，每节 5-10 行 + 一个配置/用例示例：
  1. **权限规则**：config.json `permissions.allow/deny/ask` 示例（含 `bash(git diff:*)`、`write_file(src/**)`、`web_fetch(domain:github.com)` 三种语法与 deny>ask>allow 优先级说明）；
  2. **Hooks**：五事件、matcher、command 型处理器配置示例、stdin JSON/exit 2 阻断协议一句话、timeout 字段、"hook allow 不绕过审批"安全说明；
  3. **子代理 task 工具**：内置 general-purpose/explore、`.nexuscli/agents/*.md` frontmatter 示例（name/description/tools）、深度 1 说明；
  4. **自定义命令增强**：frontmatter `mode`/`allowed-tools`/`argument-hint` 示例、`$1..$9` 与 `` !`cmd` ``（含高危拒绝说明）；
  5. **/compact**：用法 `/compact [focus]` 与统计输出说明；
  6. **plan 模式**：Shift+Tab 三态循环与只读约束说明。
- 文档语言与现有 README 一致（中文为主）；代码块标注 json/markdown/bash。

## TUTORIAL.md
- 在合适的教程阶段补一小节"平台能力速览"：三段式示例走一遍（配置 permissions + hooks → 定义自定义命令 → Shift+Tab 进 plan 模式审阅 → /compact 收尾），保持教程既有口吻。

## 验证
- `uv run python -m ruff check .`（README 内代码块不影响 ruff，但跑一遍确认没改坏别的）；README 示例中的 config 键名必须与实现一致（对照 `src/nexuscli/config.py` 的字段名与 `design/` 各简报的 schema，不确定就读源码，不要凭记忆写）。
- 不改代码逻辑（repl.py 仅允许 `/help` 文本与列表项补充）；不动 `.spec/`。
