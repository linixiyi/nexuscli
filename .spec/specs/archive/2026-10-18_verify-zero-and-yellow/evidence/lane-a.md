# Lane A — 会话与快照 真实运行证据(2026-10-18)

运行环境:每场景独立 `RUNHOME=$(mktemp -d)`,拷入 `~/.nexuscli/config.json` + `models.json`,以 `HOME=$RUNHOME` 运行真实 `.venv/bin/nexuscli`(REPL 表面用 pty 驱动真实进程,驱动脚本见 `lane-a-raw/pty_driver.py`,原始输出在 `lane-a-raw/*.log`)。网关:真实 cbcn/glm-5.3-flash。

机器验证新增:`tests/test_lane_a_session_surface.py`(7 项)、`tests/test_lane_a_snapshot_restore.py`(8 项)。本轮命令 `.venv/bin/python -m pytest tests/test_lane_a_session_surface.py tests/test_lane_a_snapshot_restore.py tests/test_snapshot.py tests/test_vfs.py tests/test_repl.py` → `44 passed, 1 skipped`(skip 为 test_vfs 的 win32-only 用例)。lane 两文件单独跑 = 15 passed。

---

## 1. [4./save] — ⚠️ 行为与矩阵描述不符(已修不了的边界:repl.py)

**怎么跑的**:pty 驱动 REPL(HOME 隔离):真实回合「请只回复两个字:好的」→ `/save 项目使用 uv 管理依赖,不用 pip` → `/exit`;再以同 RUNHOME 跑 `nexuscli sessions`。

**关键输出**(lane-a-raw/save.log):
```
❯ /save 项目使用 uv 管理依赖,不用 pip
Saved memory #1
$ nexuscli sessions
1  20261018-203729-9ad01e  2 msgs  2026-10-18 20:38  请只回复两个字:好的
```
sqlite 证实 `/save` 落的是**长期记忆**:`(1, '/private/tmp/laneA-s1/project', '项目使用 uv 管理依赖,不用 pip', 'manual', 0.8)`。

**结论**:提示可读(`Saved memory #1` ✓),sessions 列表能看到会话 ✓——但那是普通回合的自动持久化(repl.py:361)造成的,**`/save` 本身是记忆命令**(`repl.py:509-519`,`/save <fact>` → MemoryManager),与矩阵行「/save 存当前会话」不符(README:307 同样记载 `/save <fact>`)。会话无 /save 也能存:开场即有一回合时列表可见。

**矩阵状态建议**:/save 实际行为已机器+真实双验;但「存当前会话」这一用户故事不成立 → 不能标 ✅,建议改矩阵行描述(或由所有方决定是否要会话级 /save)。

## 2. [4./resume] — ✅

**怎么跑的**:进程 A 真实回合埋暗号「柠檬七号」后 `/exit`;新进程 B:`/resume`(无参列表)→ `/resume 1` → 真实回合「暗号是什么?」→ `/exit`。

**关键输出**(lane-a-raw/resume_b.clean):
```
│ 1 │ 20261018-204004-3b374f │ 听好了,暗号是柠檬七号。… │ 4 │ 2026-10-18 20:40 │
Resume with /resume <index-or-id>
Resumed session 20261018-204004-3b374f (4 messages) — 听好了,暗号是柠檬七号。…
⏺ 柠檬七号
```
**结论**:列表可读、恢复行带 id/条数/标题,跨进程接上上文(正确答出暗号)。矩阵建议:✅(机器验证已有 test_repl.py:94-175 三条,未重写)。

## 3. [8.3 / 4./fork] — ✅(本轮补了机器断言)

**怎么跑的**:pty 驱动:真实回合(暗号苹果九号)→ `/fork 实验方向` → 在分叉里再真实回合(改暗号梨十号)→ `/exit`;随后直接检查两个 transcript 文件 + `sessions --all`。

**关键输出**(lane-a-raw/fork.log):
```
Forked session 20261018-204223-6afcbf → 20261018-204311-f42b5a (2 messages) — 实验方向
原会话 6afcbf: msgs=2 forked_from=''   → 只有分叉前的 user+assistant
分叉   f42b5a: msgs=4 forked_from=6afcbf, title='实验方向' → 分叉前 2 条 + 分叉后 2 条
```
**结论**:主线会话历史在分叉后逐字节未变(分叉回合只进了分叉),链接与标题正确,确认行可读。机器断言:test_lane_a_session_surface.py(fork 输出/链接/usage 基线清零/源冻结/空历史提示)。矩阵建议:✅。

## 4. [4./snapshot] — ✅(本轮补了命令级机器断言 + 修了缺口)

**怎么跑的**:pty 驱动 REPL 于临时项目(note.txt=v1):`/snapshot save 改坏前` → `/snapshot` → `/exit`。

**关键输出**(lane-a-raw/snap_save.log):
```
❯ /snapshot save 改坏前
Snapshot saved: 改坏前_20261018124420929514
1. 改坏前_20261018124420929514 改坏前 2026-10-18T12:44:20.930665+00:00
```
**结论与修复**:跑之前发现**真缺陷**——`snapshot_cmds.py` 原本只能 list/clean,`features.auto_snapshot` 默认关时用户没有任何手动建快照的入口,`/restore`、vfs 全部无米下锅(矩阵用户故事不可能完成)。已在边界内修:`/snapshot save [label]`(label 经 `\w.-` 白名单清洗成目录名片段,防路径注入),空列表提示指向用法;顺带修了 rich markup 吞掉 `[label]` 字样的显示 bug(机器断言抓住)。矩阵建议:✅(描述建议写成「/snapshot save 存快照;/snapshot 列表」)。

## 5. [4./restore] — ✅(本轮补了命令级机器断言)

**怎么跑的**:接场景 4,进程外把 note.txt 改成 `TOTALLY BROKEN`,新 REPL 进程 `/restore 1` → `/snapshot` → `/exit`;另跑 `/restore 99` 与裸 `/restore` 错误路径。

**关键输出**(lane-a-raw/snap_restore.log、restore_err.log):
```
Restored 改坏前_20261018124420929514
$ cat note.txt → line-one v1 / line-two      # 内容恢复到快照时点
Error: snapshot not found: 99                 # 错误路径可读,无 traceback
Usage: /restore <snapshot-id-or-index>
```
**结论**:回滚后文件内容与快照时点一致;恢复后列表新增 `pre-restore_…` 安全快照(service 既有行为)。机器断言:test_lane_a_snapshot_restore.py(用法/按序号/按 id/坏引用不伤工作区/跨进程恢复)。矩阵建议:✅。

## 6. [8.4 快照机制整体链路] — ✅(真实走通,未重写 test_snapshot.py)

**怎么跑的**:同场景 4/5 的真实链路:`/snapshot save`(create→索引落盘 index.jsonl→工作树拷贝含 note.txt)→ 进程外改坏 → **新进程** `SnapshotService` 按 index 解析 `restore("1")` → 先建 pre-restore 安全快照 → 工作区回滚 → 文件内容与快照时点一致。
**关键输出**:`index.jsonl` 行 `{"id": "改坏前_…", "phase": "改坏前", "path": "…/36ac7263195abcde/改坏前_…", "skipped": []}`;快照目录内 `note.txt` = v1;回滚后 `cat note.txt` = v1。
**结论**:create→index→resolve→restore 全链路在真实进程、跨进程下走通。矩阵建议:✅。

## 7. [8.5 vfs_ls/vfs_read] — ✅(真实网关,模型调用两个工具)

**怎么跑的**:快照(v1 内容)→ 进程外把 note.txt 改成 v2 → 新 REPL 真实回合让 agent 用 vfs_ls/vfs_read 对比(lane-a-raw/vfs_read3.log;前两次尝试 vfs_read.log/vfs_read2.log 因模型 thinking 超出观测窗口被截,工具结果已可见,第三次补全整回合)。

**关键输出**:
```
⏺ vfs_read(path=/note.txt, snapshot=1)
  ⎿ vfs_read: 1: v1-first-line  (3 lines)
⏺ v1-first-line
  second line
  third line
```
(此刻工作区 note.txt = `v2-CHANGED-CONTENT`,回答是快照里的 v1。)
**结论**:只读视图经真实模型调用返回改前内容,工具行与最终回答可读;未误读工作区现值。矩阵建议:✅。

---

## 缺陷与反馈

**fixes(边界内已修+回归)**:
1. `src/nexuscli/entrypoints/commands/snapshot_cmds.py`:`/snapshot` 无法创建快照(默认配置下手动快照入口不存在)→ 新增 `/snapshot save [label]`(label 清洗为安全目录名片段),空列表提示用法;机器断言 5 条(save 确认/默认 label/label 注入防护/列表可读/clean 计数)。
2. 同文件:空列表提示里 `[label]` 被 rich 当 markup 吞掉(用户会看到 `/snapshot save )`)→ `\[label]` 转义;由 test_snapshot_listing_is_readable_and_empty_state_hints_save 抓住并回归。

**feedback(跨边界)**:
1. **high** — `src/nexuscli/entrypoints/repl.py:509-519` `/save` 是长期记忆命令,与测试矩阵「/save 存当前会话,sessions 列表能看到」不符(sessions 列表可见性来自回合自动持久化,非 /save)。需矩阵所有方裁决:改矩阵行描述(README:307 与实现一致)或实现会话级 /save(改动点在 repl.py,本 lane 边界外)。
2. **low** — `docs/testing-matrix.md` 4./snapshot 行描述「手动存快照」应更新为「/snapshot save [label] 存快照;/snapshot 列表/clean」(本轮已补齐能力)。
3. **info** — 真实网关 glm-5.3-flash thinking 阶段可达 60s+,pty 驱动多轮表面(/team、/expert)需要 ≥150s/回合的观测窗口。
