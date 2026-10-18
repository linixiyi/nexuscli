# Lane E 证据 — 工具面与 MCP(2026-10-18 verify-zero-and-yellow)

执行者:工具面与 MCP lane。日期:2026-10-18。环境:darwin arm64,真实网关 `http://localhost:20128/v1`(cbcn/glm-5.3-flash,配置经隔离 RUNHOME 拷贝)。所有真实运行的 HOME 均为 `mktemp -d` 隔离目录,未触碰真实用户数据。

机器验证命令(本轮全部真实跑过,22 passed):

```
.venv/bin/python -m pytest -q tests/test_lane_e_tool_surfaces.py tests/test_lane_e_mcp_sdk.py
# ......................   [100%]  (22 passed)
.venv/bin/ruff check tests/test_lane_e_tool_surfaces.py tests/test_lane_e_mcp_sdk.py src/nexuscli/tools/file_ops.py
# All checks passed!
```

邻接回归(修改 file_ops.py 后确认未破坏既有断言,非全量):

```
.venv/bin/python -m pytest -q tests/test_tools.py tests/test_mcp_oauth.py tests/test_mcp_filter.py \
  tests/test_model_selector.py tests/test_todo_store.py tests/test_search_rank.py
# ...................................................   [100%]  (51 passed)
```

---

## 1. [5.directory_tree] — ✅(原 ⬜ → 建议 ✅)

- 机器:`tests/test_lane_e_tool_surfaces.py::test_directory_tree_renders_readable_tree`、`::test_directory_tree_max_depth_and_error_copy` — 断言连接符 `├──/└──`、目录带 `/`、`.git` 等跳过目录隐藏、max_depth 生效、错误文案 `Not a directory:`。
- 真人模拟:隔离 RUNHOME + 临时项目(docs/ src/ note.bin README.md .git/),`HOME=$RUNHOME .venv/bin/nexuscli -p '用 directory_tree 工具(参数 path=".", max_depth=2)…'`,关键输出:

```
proj/
├── docs/
│   └── guide.md
├── src/
│   └── app.py
├── note.bin
└── README.md
```

- 结论:树正确、可读,`.git` 被排除,深度正确。矩阵建议:✅。

## 2. [5.get_file_info] — ✅(本轮修复一处真缺陷;原 ⬜ → 建议 ✅)

- 【已修】缺陷(边界内):`src/nexuscli/tools/file_ops.py:588` 原来输出 `Modified: 1792326641`(裸 epoch),用户问“什么时候改的”读不懂。已改为 `Modified: 2026-10-18 20:36:20 (epoch 1792326981)`(file_ops.py `_format_mtime`),回归测试 `test_get_file_info_reports_readable_metadata` 钉住。
- 真人模拟:3KB 文件,`-p 'note.bin 这个文件多大、最后一次修改是什么时候?…'`,关键输出(与 `stat` 的 `3072 bytes mtime=Oct 18 20:36:20 2026` 一致):

```
Path: note.bin
Type: file
Size: 3.0KB
Modified: 2026-10-18 20:36:20 (epoch 1792326981)
Permissions: 0o644
```

- 结论:元信息正确且人类可读。矩阵建议:✅。

## 3. [5.search_code] — ✅(原 ⬜ → 建议 ✅)

- 机器:`test_search_code_without_index_gives_index_hint`(空索引 → `(no indexed matches; run /index first)`)、`test_search_code_after_indexing_returns_ranked_hits`(命中行 + `[hits]` 统计)。
- 真人模拟:临时项目 `src/app.py` 含 `parse_config`,`HOME=$RUNHOME python -c "CodeIndex(proj).rebuild()"`(19 行,等价 /index),再 `-p '用 search_code 工具搜索 parse_config…'`:

```
src/app.py:1: def parse_config(path):
src/app.py:6: cfg = parse_config("app.toml")
[hits] matched 2 line(s) across 2 indexed candidate(s); showing top 2; terms: parse_config
```

- 结论:先建索引后语义检索返回相关命中,排名与统计可读。矩阵建议:✅。

## 4. [5.todo_write/todo_read] — ✅(原 ⬜ → 建议 ✅)

- 机器:`test_todo_write_then_read_round_trip_is_readable`(写读往返逐字一致、图标 ☐/▸/☑、todo.json 落盘)、`test_todo_write_invalid_payload_copy`(错误文案)。
- 真人模拟:`-p '用 todo_write 建立任务清单…然后用 todo_read 读回,把读回的清单原样贴出来'`:

```
1. ▸ [in_progress] 梳理目录结构 (high)
2. ☐ [pending] 写总结
```

读回与写入一致;`$PROJ/.nexuscli/todo.json` 落盘内容匹配。REPL 展示即该工具结果,可读。矩阵建议:✅。

## 5. [4./hitl] — ✅(原 ⬜ → 建议 ✅)

- 机器:`test_hitl_switches_mode_and_prints_label`(auto → mode=auto 且 `policy.hitl_mode=never`)、`test_hitl_bad_argument_shows_usage_and_keeps_mode`、`test_permission_mode_label_covers_plan`。
- 真人模拟:pty 驱动真实 REPL(脚本 /tmp/lane_e_pty.py,一次性、未提交),关键输出:

```
/hitl        → Permission mode: Default
/hitl auto   → Permission mode: Auto (full access)
状态栏:cbcn/glm-5.3-flash  ░░░░░░░░░░░░ 0%  Default  ⇧Tab
      → cbcn/glm-5.3-flash  ░░░░░░░░░░░░ 0%  Auto (full access)  ⇧Tab
/hitl nonsense → Usage: /hitl default|auto
```

- 结论:切档、状态栏、错误提示三者联动正确。矩阵建议:✅。

## 6. [4./model] — ✅(原 ⬜ → 建议 ✅)

- 机器:`test_activate_model_without_key_names_provider_env_vars`(无密钥切换时警告必须点名 `DEEPSEEK_API_KEY, NEXUSCLI_API_KEY`,且切换本身生效)、`test_model_command_argument_form_switches_and_reports`(`/model <provider> <model>` 参数形态)。
- 真人模拟:pty 驱动 TUI 选择器,三个场景:
  1. 打开选择器(当前模型是 models.json 里的自定义项,选择器正确落在 Custom 页并以 `✓` 标记当前)→ Shift+Tab 切到 Default 页 → Down 光标移到 `> DeepSeek V4 Pro` → Up 移回 `> DeepSeek V4 Flash` → Enter:
```
Switched to deepseek-v4-flash, but no API key is configured for provider deepseek. Set one of: DEEPSEEK_API_KEY,
NEXUSCLI_API_KEY
Switched model: DeepSeek V4 Flash (deepseek/deepseek-v4-flash)
```
  2. Esc:选择器打开后按 Esc → 回到 `Type your message` 输入行,全程无 "Switched model",状态栏模型名不变。
  3. 参数形态 `/model deepseek deepseek-chat` 亦由机器测试覆盖。
- 结论:上下选、回车切换、Esc 取消、无密钥报对环境变量名,全部正确。矩阵建议:✅。

## 7. [4./mcp] — ⚠️ 仍有问题(原 ⬜ → 建议 🟠/保持问题行)

- 真人模拟:临时项目 `.nexuscli/mcp.json` 配真实 stdio server(FastMCP,echo/ping/secret 三工具),pty 启动 REPL:banner 显示 `1 MCP server · Tools 32`(server 真实拉起、工具真实注册),`/tools` 可见 `mcp__demo__echo`。
- 但 `/mcp` 本身只输出一行提示(repl.py:685-689),不展示已接 server 与工具:

```
❯ /mcp
Use `nexuscli mcp serve --transport stdio|http --port 3000` to expose tools.
```

- 结论:与矩阵预期「/mcp 展示已接 server 与工具」不符。repl.py 是共享只读文件 → 已写 feedback,不在本轮改。矩阵建议:保持 ⬜(显示面未实现),但 MCP 接入本身已验证。

## 8. [5.edit_file] — ✅(原 🟡 → 建议 ✅)

- 机器(新增,原无直接行为断言):`test_edit_file_replaces_exactly_once_and_reports`(只替换第一处、diff 摘要、未命中文案)、`test_edit_file_dry_run_leaves_file_untouched`。
- 真人模拟:`printf 'DEBUG = False\nLEVEL = 1\n' > config.py`,`-p --yes '把 config.py 里的 DEBUG = False 改成 DEBUG = True,用 edit_file 工具…'`,工具返回:

```
Edited config.py
-DEBUG = False
+DEBUG = True
```

落盘验证:`DEBUG = True` + `LEVEL = 1`(第二行未动)。矩阵建议:✅。

## 9. [5.revert_turn] — ✅(原 🟡 → 建议 ✅)

- 真人模拟:pty REPL(真实网关):`/snapshot save before-edit` → `Snapshot saved: before-edit_20261018124408884811`;`/hitl auto` 后发「用 write_file 把 note.txt 覆盖成 goodbye」→ `Wrote note.txt`;再发「用 revert_turn 工具恢复到快照 1」→ `Restored snapshot before-edit_20261018124408884811`(同一 id);退出后 `cat note.txt` = `hello`(改前内容)。
- 结论:撤销上一轮改动,文件精确回到改前。矩阵建议:✅。

## 10. [10.2 MCP 工具过滤] — ✅(原 🟡 → 建议 ✅)

- 机器:既有 `tests/test_mcp_filter.py`(fake 远端工具);本轮新增**真实 stdio 子进程**端到端:`test_lane_e_mcp_sdk.py::test_allowed_tools_whitelist_visible_over_real_stdio`、`::test_whitelisted_tool_round_trips_over_real_stdio`(mcp.json `allowed_tools: ["echo"]`,FastMCP 子进程真实握手,echo 真实往返 `echo: lane-e`)。
- 真人模拟:同一 mcp.json 配置,pty REPL `/tools`:

```
mcp__demo__echo                 ← 可见
mcp__demo__get_prompt / list_prompts / list_resources / read_resource   ← 虚拟视图照常
# mcp__demo__ping、mcp__demo__secret 均不可见(白名单外被滤掉)
```

banner `Tools 32` = 27 内置/vfs + 1 白名单内工具 + 4 虚拟视图,数字吻合。
- 结论:allowed/blocked 真实生效,只放行的工具可见。矩阵建议:✅。

## 11. [10.3 OAuth 令牌落盘] — ✅(原 🟡 → 建议 ✅,附环境边界说明)

- 机器(新增):`test_token_file_written_with_0600_permissions`、`test_oauth_flow_against_local_fake_server`(本地假 OAuth server:PKCE exchange → 存盘 → 新鲜 token 走缓存 → 过期 token 走 refresh,两次真实 HTTP)、`test_default_transport_refuses_loopback_token_endpoint`、`test_httpx_reaches_local_fake_server_through_hostname`。
- 真人模拟(一次性脚本,真实 httpx HTTP):

```
GUARD-REFUSES-LOOPBACK: OAuth token endpoint must not target a local/reserved address: http://127.0.0.1:63382/token
EXCHANGE-GRANT: authorization_code | pkce_sent: True   → ACCESS-TOKEN: access-1
TOKEN-FILE: $RUNHOME/.nexuscli/mcp-oauth.json  PERMISSION: 0o600
FRESH-TOKEN-CACHED: True ; STALE-REFRESH-GRANT: refresh_token -> access-2
ls -l → -rw-------  … mcp-oauth.json
```

- 边界说明(非造假,如实记录):`_validate_token_url` 按 SSRF 设计拒绝一切本地字面地址(localhost/loopback/RFC1918),故本地假 server 通过公网 DNS 名 `localtest.me`(解析落回本机)走默认 HTTP 传输完成真实流程;直连 `127.0.0.1` 的拒绝行为本身也作为真实证据记录。
- 结论:流程完整、令牌文件 0600。矩阵建议:✅。

## 12. [11.9 SDK] — ✅(原 ⬜ → 建议 ✅)

- 机器(新增):`test_create_default_engine_builds_usable_engine`(引擎创建、25 工具注册、默认 deepseek 档、system prompt 就绪;离线断言,不打网关)。
- 真人模拟:`HOME=$RUNHOME python -c` 真实 import + 创建 + 一个 turn:

```
engine: QueryEngine | model: cbcn/glm-5.3-flash | tools: 25
turn result text: 'OK'
turns: 1 | total_tokens: 5197
```

- 结论:`from nexuscli.sdk import create_default_engine` 真实创建引擎并跑通一个 turn。矩阵建议:✅。

---

## 本轮 fixes(边界内)

1. `src/nexuscli/tools/file_ops.py` — `get_file_info` 的 `Modified:` 由裸 epoch 改为「本地时间 + (epoch N)」;真实网关运行与回归测试双向验证。

## 本轮 feedback(跨边界)

1. `src/nexuscli/entrypoints/repl.py:685-689` — REPL `/mcp` 只打印 `nexuscli mcp serve` 提示,不列出已接 MCP server 与工具;与矩阵「/mcp 展示已接 server 与工具」不符(真实 pty 证据见第 7 节)。建议后续把 `McpClientManager.specs/last_errors` 与各 server 工具数渲染出来。
