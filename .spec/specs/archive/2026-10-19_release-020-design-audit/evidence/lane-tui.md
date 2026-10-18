# lane-tui 证据 — 边框 Table 裁决落地 + 新表面风格审查

分支 spec/2026-10-19_release-020-design-audit。方向依据:PRODUCT.md:25-26(前景单色、ANSI 语义色、无 hex)与 rich_renderer.py:13-16(无 Panel、无 box)。

## 1. 边框 Table → 无框呈现(3 处,5 张表)

按 ask 指定的 /history、/sessions、/usage 三处落地;语言与 repl.py:667-684 的 /usage 行网格、repl.py:528 的 /config 摘要(`Table(..., show_header=False, box=None)`)同源:粗体标题行 + `Table.grid(padding=(0, 2))` + 一行 dim 表头,零 box/边框字符。

| 表面 | 旧(边框) | 新(无框) |
|---|---|---|
| /history | session_cmds.py:114 `Table(title="NexusCLI History")` + 4×`add_column` | session_cmds.py:117 `console.print("[bold]NexusCLI History[/bold]")` + `Table.grid(padding=(0, 2))`(session_cmds.py:118-133),首行 `"[dim]ID[/dim]"` 等表头 |
| /sessions(/resume 无参) | session_cmds.py:136 `Table(title="NexusCLI Sessions")` + 5×`add_column` | session_cmds.py:140 `console.print("[bold]NexusCLI Sessions[/bold]")` + `Table.grid(padding=(0, 2))`(session_cmds.py:141-160),dim 表头 `# / ID / Title / Messages / Updated` |
| /usage stats 汇总 | usage.py:57 `Table(title=f"NexusCLI Usage — last {days} day(s)")` | usage.py:62 标题行 + `Table.grid(padding=(0, 2))`,标签列 `add_column(style="dim")`(与 /usage 行网格同语言,usage.py:63-73) |
| /usage stats By model | usage.py:65 `Table(title="By model")` | usage.py:76-79 `[bold]By model[/bold]` 标题行 + 网格,dim 表头 Model/Turns/Tokens/USD/CNY(usage.py:80-107) |
| /usage stats By source | usage.py:86 `Table(title="By source")` | usage.py:113-116 `[bold]By source[/bold]` 标题行 + 网格,dim 表头 Source/Turns/Tokens/USD/CNY(usage.py:117-138);空行仍不渲染(保持 test_usage_coverage.py:390/420 契约) |

保留的对外字面子串(边界外测试依赖):`NexusCLI Usage`、`last 7 day(s)`、`turns`、`By model`、`By source`、`By source` 缺席语义——test_usage_coverage.py:377/390/391/420 与 test_lane_f_observability.py:315-317 全部继续成立。

实测渲染(Custom scratch Console,width=100)摘录:

```
NexusCLI History
ID                      Title              Match              Updated
20261019-011229-bd41fa  fix the login bug  fix the login bug  2026-10-19 01:12
Searched this project. Resume with /resume <id>

NexusCLI Usage — last 7 day(s)
turns                    1
...
By source
Source  Turns  Tokens       USD       CNY
repl        1      15  0.000000  0.000000
```

## 2. 新表面风格审查(4 处,全部合规,零修改)

约束:只用前景色、ANSI 语义色、无 hex 字面量、无背景色。全量扫描命令与结果:

```
grep -nE "#[0-9a-fA-F]{3,8}\b|bg:|on (black|white|red|blue|grey)" \
  src/nexuscli/entrypoints/commands/{session_cmds,usage,approval,snapshot_cmds}.py \
  src/nexuscli/entrypoints/{model_selector,repl}.py src/nexuscli/render/rich_renderer.py
→ exit=1(无匹配;src/ 内仅有的两处 hex 命中均为注释,repl.py:280、rich_renderer.py:20)
```

- **/mcp server 网格** repl.py:766-804:`Table.grid(padding=(0, 2))`(:788)、`add_column(style="bold")`(:789)、`[dim]{spec.type}[/dim]`(:794)、`[red]error:[/red]`(:803)——无框、前景 dim/red,合规。
- **审批 Reason 行** approval.py:59:`console.print(f"[dim]Reason: {description}[/dim]")`——前景 dim,合规。(approval.py 在本 lane 边界外,合规故无需改。)
- **/snapshot save 确认** snapshot_cmds.py:40:`[green]Snapshot saved:[/green] {record.id}`——ANSI 语义绿,合规。
- **模型列表 ✓ 徽标** model_selector.py:169 `check = " ✓"`、:246 `badges += " · 视觉✓"`——纯文本字形,样式走 model_selector.py:337-356 前景 ANSI 类(`bold ansigreen`/`ansibrightblack` 等),无 hex、无 bg,合规。

## 3. 契约变更清单(断言 ↔ 实现成对)

实现侧(上表 5 张表)。断言侧——旧断言无一对旧边框字符取 pin(grep `╭╰├│─` 于 tests/ 仅命中 test_lane_e_tool_surfaces.py:78 的文件树连接符,与本改动无关),故为「收紧 + 新增 pin」,无删除断言:

- tests/test_history_search.py:`test_history_slash_lists_hits_with_resume_hint` 新增 `assert "NexusCLI History" in text` + `assert not _has_box_drawing(text)`(新增模块级 `_has_box_drawing`,tests/test_history_search.py:35-37、:171-181)。
- tests/test_repl.py:`test_handle_slash_resume_without_arg_lists_sessions` 新增 `assert "NexusCLI Sessions" in text` + `assert not _has_box_drawing(text)`(tests/test_repl.py:25-27、:155-159)。
- tests/test_usage_history.py:`test_usage_slash_keeps_turn_display_and_adds_stats` 中 `assert "Usage" in stats_text` → `assert "NexusCLI Usage" in stats_text`;新增 `assert "By model" in stats_text` 与 `assert not _has_box_drawing(stats_text)`;`"last 7 day(s)"` 断言保持(tests/test_usage_history.py:77-80、:201-217)。
- tests/test_render.py、tests/test_session_store.py:无受影响断言(前者只 pin 渲染器事件流 banner/toolbar/tool 线;后者 pin /fork 文案 `Forked session` 与 store 行为),零改动——如实说明。

## 4. 检查记录(全部本会话实际执行)

- 门槛(ask 指定命令):`.venv/bin/python -m pytest -q tests/test_render.py tests/test_repl.py tests/test_history_search.py tests/test_session_store.py tests/test_usage_history.py` → **65 passed in 0.40s**。
- 边界外共享该表面的 3 个文件定向验证(非全量):`.venv/bin/python -m pytest tests/test_usage_coverage.py tests/test_repl_mcp_status.py tests/test_lane_f_observability.py` → **18 passed in 0.40s**。
- ruff:`.venv/bin/ruff check` + `ruff format --check`(5 个改动文件)→ All checks passed / 5 files already formatted。
- 视觉验证:上节 scratch Console 实渲染,输出无 box 字符、dim 表头生效、列对齐正常。

## 5. 边界外发现(feedback 级,未改)

- repl.py:465(/context)、repl.py:500-508(/compact)、repl.py:578(/audit)仍是无参 `Table(title=...)` = 默认粗框——与 /history /sessions /usage 同类违规,但不在本 ask 的三处清单内,留给下一轮(注意 /audit 有 test_lane_c_modes.py 等契约需同步核对——未验证具体断言,仅指出位置)。
- PRODUCT.md:53 称 repl.py 现状「无 Table」与事实不符(repl.py 多处 Table;本 lane 落地后仍余上述 3 张边框表),PRODUCT.md 由其他 lane 在改,不越界。
- ask 称证据目录已存在,实际不存在,已 `mkdir -p` 后写入本文件。
