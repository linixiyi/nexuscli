# Round 2 — TUI 收口 lane(/context /compact /audit 无框化)

日期:2026-10-19。范围:`src/nexuscli/entrypoints/repl.py` 三处带框
`Table(title=…)` 改为与 /config /policy 摘要及第一轮 /history /sessions 一致的
无框语言(bold 标题行 + `Table.grid(padding=(0, 2))`,/audit 另加 dim 表头行),
受影响断言先行 grep 再同步。未触碰 PRODUCT.md、/usage stats 视图
(`usage.py`)、默认 /usage 网格(repl.py:667-684 改动后行号)。

## 契约变更清单(旧行 → 新行)

- `src/nexuscli/entrypoints/repl.py:465-474`(/context)
  `Table(title="NexusCLI Context")` + `add_column("Field"/"Value")` + 6×`add_row`
  → `[bold]NexusCLI Context[/bold]` 标题行 + `Table.grid(padding=(0, 2))`
  (首列 `style="bold"`,无表头)+ 同样 6 行 `grid.add_row`。
- `src/nexuscli/entrypoints/repl.py:499-508`(/compact 压缩成功分支)
  `Table(title="NexusCLI Compaction")` + Field/Value 列 + 3-4×`add_row`
  → `[bold]NexusCLI Compaction[/bold]` 标题行 + 同构无框网格,tokens before /
  tokens after / summarized messages / focus(focus 仍按条件行)。
- `src/nexuscli/entrypoints/repl.py:577-589`(/audit 非空分支)
  `Table(title=f"NexusCLI Audit — last {len(events)}")` + 4 列
  → `[bold]NexusCLI Audit[/bold] [dim]— last N[/dim]` 标题行 +
  `Table.grid(padding=(0, 2))`,首行 dim 表头 `time tool outcome approver`,
  行数据(time 截 `HH:MM:SS`、tool/outcome/approver)不变。

## 受影响断言(grep 先行)

- 三个许可测试文件内 grep `NexusCLI Context|NexusCLI Compaction|NexusCLI
  Audit|compact|context window|approver`:**无**断言 pin 旧带框串。
  task 提到的「test_repl_ux.py 第一轮 /audit 4 列摘要表断言」实际不存在:
  `tests/test_repl_ux.py:99-108`(改动前行号)只有
  `test_audit_prints_a_table_not_json`,断言空路径 `(no audit records)`,
  不受本次渲染影响(已原样跑过,通过)。
- 许可文件之外有两处 pin 标题串,不在本 lane 修改范围,改动后实测仍绿:
  `tests/test_slash_surfaces_docs.py:80`(`NexusCLI Context`)、
  `tests/test_integration_platform.py:271`(`NexusCLI Compaction`)。
  因实现保留同串标题行,这两处无需改动即通过(见下方运行记录)。
- 断言与实现成对新增(许可文件内):
  - `tests/test_repl_ux.py` 新增 `test_audit_with_records_is_a_frameless_four_column_summary`:
    `AuditLog.record` 播 1 条 → `/audit` 断言 `NexusCLI Audit`、`— last 1`、
    `time/tool/outcome/approver` 四表头、行值,且 `not _has_box_drawing(out)`。
    (即 task 所指「/audit 4 列摘要表」契约的落点。)
  - `tests/test_repl.py` 新增 `test_handle_slash_context_is_a_frameless_summary`、
    `test_handle_slash_compact_is_a_frameless_summary`:直驱 `_handle_slash`,
    断言标题、六/四行标签、`not _has_box_drawing`,/compact 另断言
    `len(agent.history) < len(history)`(16 turn 长历史,沿用
    test_integration_platform 的构造)。

## 命令与输出摘录

```
$ .venv/bin/python -m pytest tests/test_repl.py tests/test_repl_ux.py tests/test_usage_history.py
..........................                                             [100%]
26 passed in 0.61s

$ .venv/bin/python -m pytest -v tests/test_repl.py::test_handle_slash_context_is_a_frameless_summary \
  tests/test_repl.py::test_handle_slash_compact_is_a_frameless_summary \
  tests/test_repl_ux.py::test_audit_with_records_is_a_frameless_four_column_summary \
  tests/test_repl_ux.py::test_audit_prints_a_table_not_json
4 passed in 0.25s

$ .venv/bin/python -m pytest -v tests/test_slash_surfaces_docs.py::test_context_table_reports_window_and_workspace \
  tests/test_integration_platform.py::test_compact_slash_command_reports_and_shrinks_history
2 passed in 0.25s
```

真实 Console 渲染(force_terminal=False,width=80),`BOX_GLYPHS_PRESENT: False`:

```
NexusCLI Context
cwd             /var/folders/.../tmpkugwn_h5
model           deepseek-v4-flash (deepseek)
context window  128000
render          inline
memory          0 recent entries
tools           3
NexusCLI Audit — last 2
time      tool        outcome  approver
17:55:28  bash        success  auto
17:55:28  write_file  denied   hitl
NexusCLI Compaction
tokens before        6657
tokens after         3253
summarized messages  26
focus                keep the login bug
```

## 结论

- /context /compact /audit 三处与 /config /policy /history /sessions /usage
  同语言:无框、bold 标题行、grid;/audit 带一行 dim 表头。
- 全绿门槛(任务指定的三文件)26 passed;两处范围外标题串断言实测仍绿。
- 全量套件未跑(按纪律留给尾段)。
