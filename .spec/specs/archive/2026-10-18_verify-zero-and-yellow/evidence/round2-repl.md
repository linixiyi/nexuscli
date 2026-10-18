# Round 2 证据 — REPL 审批与 MCP 复验 lane(2026-10-18)

执行者:REPL 审批与 MCP 复验 lane。环境:darwin arm64,真实网关 `http://localhost:20128/v1`(cbcn/glm-5.3-flash)。所有真实运行的 HOME 均为 `mktemp -d` 隔离目录(配置从真实用户配置拷贝后按场景改 RUNHOME 内副本),未触碰真实用户数据。pty 驱动为一次性脚本(/tmp/nexuscli-r2/pty2.py + run_*.py,未提交),全部 transcripts 留存在 /tmp/nexuscli-r2/run_*_full.txt。

复验对应代码:F1 守卫 `src/nexuscli/tools/builtins.py:627-687`(650 /dev/null 豁免、657-663 allow 放行);F3 `src/nexuscli/entrypoints/repl.py:766-804`(`_mcp_status_command`);V `src/nexuscli/entrypoints/commands/snapshot_cmds.py:26-59` + `src/nexuscli/entrypoints/repl.py:712-719`。

机器回归(本轮真实跑过):

```
.venv/bin/python -m pytest -q tests/test_write_scope_allow.py tests/test_repl_mcp_status.py tests/test_lane_a_snapshot_restore.py
# ............   [100%]  (12 passed)
```

---

## 1. [F1@写域守卫放行 allow] — ✅ pass

**1a. allow=["bash"],真实 -p(无审批回调路径)**:`RUNHOME_A/.nexuscli/config.json` 在用户配置副本上加 `"permissions": {"allow": ["bash"]}`,`HOME=$RUNHOME_A .venv/bin/nexuscli -p '用 bash 工具原样执行这条命令…:echo allow-path-ok > /private/tmp/nexuscli-r2-allow-probe.txt 2>/dev/null; echo GUARD_OK'`(/private/tmp 在 workspace 与 tempfile.gettempdir()=/var/folders/…/T 之外,修复前会被守卫按"工作区外写目标"fail-closed 拒绝)。关键输出:

```
输出如下:
```
GUARD_OK
```
--- probe file:
allow-path-ok            ← cat 落盘文件,命令真实执行
```

无任何 "write-scope" 拒绝文案。对照语义源:lane-c.md:181(修复前 executor 直接复现 `prompted: 1` + "write target outside the workspace: /dev/null")。

**1b. 无 allow,真实 REPL pty(RUNHOME_B,Default 模式)**:让模型原样执行 `echo probe > /private/tmp/nexuscli-r2-outside-probe.txt 2>/dev/null; echo WROTE_OUTSIDE`。用户看到两次审批(第一次 executor 审批,答 y;第二次写域审批,答 n),随后写域拒绝文案原样可见:

```
⏺ bash(command=echo probe > /private/tmp/nexuscli-r2-outside-probe.txt 2>/…)
Approval required bash (medium) command=echo probe > /private/tmp/... 2>/dev/null; echo WROTE_OUTSIDE
▌ Command rejected by write-scope approval. Static analysis found a write target outside the workspace:
▌ /private/tmp/nexuscli-r2-outside-probe.txt
```

落盘核验:`ls /private/tmp/nexuscli-r2-outside-probe.txt` → 不存在(拒绝真实拦住了写)。矩阵 6.3(allow 放行)/6.4(无 allow 仍审批/拒绝)语义均未回归。

**verdict: pass**

## 2. [F1@/dev/null 豁免] — ✅ pass

无 allow 配置(RUNHOME_B),纯读命令 `ls notes.txt 2>/dev/null; echo READ_OK/SILENT_OK`,两种模式:

- **Default(auto)模式**(run_b2a,mark 圈定本回合区域):恰好 **1** 次审批提示(即 bash 普通审批;修复前 /dev/null 会被守卫当成外域写目标再弹一次写域审批),答 y 后命令真实执行:

```
⏺ bash(command=ls notes.txt 2>/dev/null; echo READ_OK)
Approval required bash (medium) command=ls notes.txt 2>/dev/null; echo READ_OK
Approve? (y / r / a / s / n, Esc denies) (n): y
  ⎿ bash: notes.txt  (2 lines)
模型最终答复:The command ran. Output is: ``` notes.txt READ_OK ```
prompts_in_turn=1;整场 transcript "write-scope" 出现 0 次
```

- **/hitl auto(never,full access)模式**(run_b2b):零审批提示,静默直通:

```
⏺ bash(command=ls notes.txt 2>/dev/null; echo SILENT_OK)
  ⎿ bash: notes.txt  (2 lines)
prompts=0;"write-scope"/"outside the workspace" 工具错误均未出现
```

**verdict: pass**(诚实说明:Default 模式下带 `2>/dev/null` 的命令仍会弹一次 bash 普通审批——这是 executor 对非静态只读命令的既有语义,不是写域审批;写域审批确实不再出现。)

## 3. [F3@/mcp 列表] — ✅ pass

RUNHOME_C/proj 的 `.nexuscli/mcp.json` 配 3 个 server:`demo`(真实 FastMCP stdio 子进程,.venv python 跑 demo_mcp_server.py,echo/ping/secret 三工具)、`off`(enabled:false)、`broken`(不存在的二进制)。真实 pty 启动 REPL(banner `2 MCP servers · Tools 34` = 27 内置 + 3 真实工具 + 4 虚拟视图),`/mcp` 输出:

```
demo    stdio /Users/linyixi/nexuscli/.venv/bin/python · 7 tools
off     stdio /bin/false · disabled
broken  stdio /nexuscli-r2-no-such-bin · enabled, 0 tools
        error: [Errno 2] No such file or directory: '/nexuscli-r2-no-such-bin'
```

未配置场景(RUNHOME_C + 无 mcp.json 的项目):`/mcp` 输出两行,提示与 serve 行齐全:

```
No MCP servers configured. Add them to .nexuscli/mcp.json (project) or ~/.nexuscli/mcp.json (user).
To expose this CLI's tools to other clients: nexuscli mcp serve --transport stdio|http --port 3000
```

server 名/类型/命令/工具数、disabled、error 行、mcp.json 提示、serve 提示全部真实可见。第一轮「/mcp 只打印一行 serve 提示」(lane-e.md:110-120)已收敛。

**verdict: pass**(低严重度观察见 feedback 2。)

## 4. [V@/snapshot 语义对齐] — ✅ pass

RUNHOME_D/proj(note.txt 内容 `hello`),真实 pty REPL 全闭环,确认文案逐条可见:

```
❯ /snapshot save before-edit
Snapshot saved: before-edit_20261018143911291617
❯ /snapshot
1. before-edit_20261018143911291617 before-edit 2026-10-18T14:39:11.293381+00:00
/hitl auto → 「用 write_file 工具把 note.txt 的内容覆盖为一行:goodbye」→ ⎿ write_file: Wrote note.txt
❯ /restore before-edit_20261018143911291617
Restored before-edit_20261018143911291617
❯ /snapshot clean
Cleaned 2 snapshots.
```

会话外核验:`cat note.txt` → `hello`(restore 把模型写入的 goodbye 真实还原)。save [label]/列表/restore/clean 四个面在真实会话里全部可读;第一轮「/snapshot 行描述过时」的修复(矩阵行语义)与实际行为一致。

**verdict: pass**

---

## 本轮 fixes(边界内)

无 — 四项复验均未发现边界内新缺陷,未改任何源码文件。

## 本轮 feedback(未收敛/低严重度观察)

1. [low] `src/nexuscli/entrypoints/commands/approval.py:50-53` — REPL 审批提示只渲染 `tool_name (danger_level) input摘要`,不渲染 `request['description']`。写域审批的 reason("Static analysis found a write target outside the workspace: …",builtins.py:665)在提示行不可见,两次审批视觉上仅 danger high/medium 之差,用户难以分辨自己在答哪一个(本轮 run_b transcript 实证:两次提示渲染同形)。矩阵 6.3/6.4 语义不受影响;若后续想让写域审批可辨识,可在该处补一行 description。
2. [low] `/mcp` 未配置分支的第二行(serve 提示,repl.py:782-785)在 pty 里比第一行晚若干秒才刷出(c2/c2b 两次真实运行:2s/6s 采样时均未出现,会话结束前可见);有 servers 时 grid 无此延迟。用户最终能看到完整两行,仅刷新时机滞后;疑与 prompt_toolkit 活动提示期间 rich 输出flush 批次有关,未深挖。

## 诚实边界说明

- run_b 首次脚本的 task3(以及 task2 的确认)曾因检测逻辑匹配到残留/回显文本而误报,已作废并在 run_b2a/run_b2b 用 mark 圈定区域重跑;上表只引用重跑后的证据。run_b 自身的 task1 证据(写域审批+拒绝文案+落盘核验)不受影响。
- run_b2b 里「写外域」那条模型自行拒绝未调用工具(模型安全行为),never 模式下守卫 fail-closed 路径未在真实会话触发;该路径由机器测试 `tests/test_write_scope_allow.py::test_without_allow_rule_still_prompts_and_fail_closed`(12 passed 之一)与 Default 模式真实审批链(run_b task1)共同覆盖。
