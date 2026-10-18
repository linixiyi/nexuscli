# Lane Orange(🟠 九断言)证据 — 2026-10-19

任务:B1 🟠 九断言 —— doctor、--version、/context、/tools、/policy、/usage、空 prompt Ctrl+C、Ctrl+D、bracketed paste 各补至少一条自动化断言。三文件全绿门禁:`.venv/bin/python -m pytest -q tests/test_doctor_surface.py tests/test_plain_exits.py tests/test_slash_surfaces_docs.py`。

## 做了什么

新建三个测试文件(边界内仅此三文件,`git status` 确认):

| 文件 | 覆盖矩阵行 | 测试 | 驱动方式 |
|---|---|---|---|
| tests/test_doctor_surface.py | 1.7 doctor | test_doctor_prints_json_with_the_five_env_sections | 子进程真实跑 `.venv/bin/nexuscli doctor`,json.loads 整段 stdout |
| tests/test_plain_exits.py | 1.9 --version | test_version_prints_the_package_version_dynamically | 子进程,动态断言 `== f"nexuscli {nexuscli.__version__}"`,不写死版本号,另断言 `nexuscli \d+\.\d+\.\d+` 形状 |
| tests/test_plain_exits.py | 3.5 | test_ctrl_c_on_empty_prompt_exits_zero | pty 驱动真 REPL,空 prompt 发 `\x03`,断言 `proc.wait()==0`(非 -2 信号死) |
| tests/test_plain_exits.py | 3.6 | test_ctrl_d_on_empty_prompt_exits_zero | pty,空 prompt 发 `\x04`,断言退出码 0 |
| tests/test_plain_exits.py | 3.10 | test_bracketed_paste_keeps_multiline_newlines | pty,发 `\x1b[200~/save alpha line\nbeta line\x1b[201~` + 回车,读回 `~/.nexuscli/memory.db` 断言存储内容为两行(`alpha line\nbeta line`) |
| tests/test_slash_surfaces_docs.py | /context | test_context_table_reports_window_and_workspace | `_handle_slash` 直驱 + Console(record=True),同 test_repl_ux.py 模式 |
| tests/test_slash_surfaces_docs.py | /tools | test_tools_lists_each_tool_on_its_own_line | 直驱,断言逐行一行一工具 |
| tests/test_slash_surfaces_docs.py | /policy | test_policy_summary_pins_the_guard_fields | 直驱,断言六字段 + permissions 行 |
| tests/test_slash_surfaces_docs.py | /usage | test_usage_renders_the_four_row_ledger | 直驱,断言 turn/session/cost/note 四行内容串 |

九个 🟠 表面每个至少一条断言,全部无真实网关请求:/context /tools /policy /usage 直驱(stub registry/agent);pty 三条 HOME 隔离 + config.json 把 base_url 钉在 `http://127.0.0.1:9/v1`(本机已关闭端口,即使按键串扰也不会外发请求),paste 用 /save 斜杠命令做接收器,不经 LLM。

## 关键命令与输出摘录

1. 门禁(ask 指定的命令,最后一轮):
```
$ .venv/bin/python -m pytest -q tests/test_doctor_surface.py tests/test_plain_exits.py tests/test_slash_surfaces_docs.py
.........  [100%]
$ .venv/bin/python -m pytest -v(同三文件)
collected 9 items
tests/test_doctor_surface.py .                [ 11%]
tests/test_plain_exits.py ....                [ 55%]
tests/test_slash_surfaces_docs.py ....        [100%]
============================== 9 passed in 9.56s ===============================
```
2. pty 稳定性:同一文件连跑 3 遍(`pytest -q tests/test_plain_exits.py` ×3 路径拼接)= 12 次执行全过,无 flake。
3. ruff(尾段全量门会跑,先自证三个新文件干净):
```
$ .venv/bin/python -m ruff check tests/test_doctor_surface.py tests/test_plain_exits.py tests/test_slash_surfaces_docs.py
All checks passed!
```
4. doctor 真实输出样例(开发机,五节均可读;测试只断言键可读,不硬断言环境有无):
```json
{"python": "3.13.13", "uv": "/Users/linyixi/.local/bin/uv", "node": "v24.13.1",
 "npx": ".../npx", "rg": ".../rg", "api_key": "configured", "provider": "openai-compatible", ...}
```

## 设计要点(与既有模式的关系)

- pty 驱动为本仓新范式(此前 🟠 pty 实测均为一次性脚本,tests/ 无既有 pty 测试);`skipif(sys.platform=='win32', reason='pty is POSIX-only')` 按 ask 原样标注,fcntl/termios/pty/select 导入收进 `sys.platform != "win32"` 守卫(Windows 收集不炸);doctor/--version 的入口点解析按平台分 bin/Scripts,并以 skipif(入口不存在) 兜底。
- 起初直驱 /context 断言整段 tmp_path 被 rich 表格在 120 列内截断(macOS tmp 路径 115 字符)——cwd 行只是回显入参,断言改用短标签,非产品缺陷(真实终端更宽,矩阵实测表格完整)。
- 手工探针曾见一次 Enter 在 paste 处理中途到达导致挂起;测试里先等 paste 回显(`beta line`)再发回车、再等 placeholder 重绘再发 Ctrl+D,竞态已闭合,连跑 3 遍无复现。

## 契约变更清单

无实现改动,零渲染字符串契约变更。新增断言 pin 的既有实现串(若后续 lane 改这些串需同步本 lane 文件):`repl.py:670-683` /usage 行内容(`turn`/`session`/`cost`/`note`、`in / out`、`turns · tokens`、`USD %.6f`);`repl.py:465-474` /context 表标题 `NexusCLI Context` 与行标签;`repl.py:547` /tools 逐行工具名;`repl.py:552-571` /policy 表标题与字段标签、`permissions:` 行;`cli.py:96-99` `nexuscli {__version__}`;`cli.py:334-346` doctor JSON 键名。注意:并行 lane D 正在改 `commands/usage.py` 的 /usage stats 视图(无框化),其默认 /usage 网格(repl.py)未被触及,本轮三文件在该工作树状态下全绿。

## 结论

九个 🟠 表面各有至少一条自动化断言,ask 指定的门禁命令 9 passed 全绿;断言未暴露产品缺陷(无需记缺陷),无真实网关请求;边界未越(仅新建三测试文件 + 本证据文件;evidence/ 目录实际不存在,按 ask 指定路径创建)。
