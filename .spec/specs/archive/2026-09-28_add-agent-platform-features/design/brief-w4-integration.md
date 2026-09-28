# 第四波 集成缝合 — 实现简报

必读：六个功能简报（brief-f1..f6）+ `design/nexuscli-context.md`；`git status`/`git diff --stat` 看本波之前各波已改文件。

## 职责
六项功能已各自落地，本波只修**集成缝隙**（都在 repl.py 与少量胶水），不顺手重构：

1. **`/compact [focus]` 接线**（repl.py）：
   - `SLASH_COMMANDS` 列表加 `"/compact"`；`_handle_slash` 加 elif：构造 `ContextWindowManager(ContextBudget(context_window=agent.llm_client.max_context_window, max_output_tokens=config.llm.max_tokens, compression_threshold=config.memory.compression_threshold, compression_target=config.memory.compression_target, reserve_tokens=config.memory.compression_reserve_tokens), max_history_messages=config.memory.max_conversation_history, min_recent_messages=config.memory.min_recent_messages, summary_max_chars=config.memory.summary_max_chars)`（照抄 agent/agent.py:200-211 的构造），调 `compact_now(agent.history, focus=arg)`，`agent.history = result.messages`，`console.print` 报告 before/after tokens 与 summarized 条数（格式对照既有 /context 输出风格）。历史为空时友好提示。
2. **自定义命令 mode/allowed-tools 应用**（repl.py `_match_custom_command` 处）：
   - `_match_custom_command` 改为 async 并改用 `await expand_command(...)`（F4 的 CommandExpansion，注意它是 async def）；
   - `expansion.mode` 非空：临时 `permission_mode.set("plan")`（仅当 mode=="plan"；"react"/"team" 对应 agent.mode 切换——agent.mode 赋值 + finally 恢复原值）；运行完 finally 恢复原 mode；
   - `expansion.allowed_tools` 非空：临时把 `agent.tool_registry` 换成只含白名单的新 `ToolRegistry`（参照 F3 的过滤构建），finally 恢复；`rejected_injections` 非空时运行前 `console.print` 提示被拒注入。
3. **交叉缝隙自查与补测**（补进对应测试文件或新建 `tests/test_integration_platform.py`）：
   - task 工具在 plan 态被拒（executor 的 plan 短路 × F3）；
   - 权限 deny × PreToolUse hook deny × plan 态三者叠加：任一命中即拒绝且原因可辨；
   - hooks SessionStart 在 REPL 启动触发一次（若 F2 波已测 agent 侧，这里只补 repl 侧触发点存在性：以可测方式抽出的函数级测试）；
   - /compact 后 hooks/权限功能不受影响（压缩不破坏后续工具调用——轻量冒烟用例）。
4. **全链路冒烟**：`uv run nexuscli --help` 正常输出（含新命令影响的入口）；`/help` 列表含 `/compact`（直接断言 SLASH_COMMANDS 常量即可）。

## 红线
- 只改 `src/nexuscli/entrypoints/repl.py`、必要时 `tests/`（新增或扩充）；发现前三波的实质 bug 先在结果里报告再最小修复（改对应模块，保持改动最小并在返回中说明）。
- 不重构 repl 的 if/elif 分发链（保持既有风格）；不动 `.spec/`；不 git commit。
- 自验：`uv run python -m pytest tests/ -q`（本波允许跑全量，因为你是最后一波实现车道）+ `uv run python -m ruff check .` + `uv run python -m ruff format --check .`。
