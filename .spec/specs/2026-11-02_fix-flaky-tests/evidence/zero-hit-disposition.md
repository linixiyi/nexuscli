# 零命中处置（accepted_risk）— 2026-11-02_fix-flaky-tests

> **v2（2026-11-02 21:31–22:23 重采）**：独立评审指出首轮采集（v1，19:45–21:08）存在
> 四项缺陷，本文件与全部证据为按修订协议重新采集的结果，**覆盖并取代 v1 的
> run-\*.log / stress-\*.log / stability-runs.json / flaky-findings.json**：
>
> 1. **v1 以 `-qq` 采集**：仓库 `pyproject.toml` 的 `addopts = "-q"` 与命令行 `-q` 合成
>    `-qq`，吞掉 pytest 总结行——39 份 v1 日志全部止于 `[100%]` 进度行，违反
>    spec「完整输出存档，不截断」。v2 修订：采集命令**不叠 CLI `-q`**，addopts 的
>    `-q` 单独生效，总结行（如 `1614 passed, 2 skipped in 415.85s`）保留在每份日志末尾。
> 2. **v1 压力轮无自证元数据**：每轮无命令、无时长、无总结行，「35 轮 exit 全 0」只能
>    靠数进度点推断。v2 修订：逐轮独立 pytest 进程，`stress-runs.json` 每轮记录
>    命令 / start / end（本地时间+epoch）/ wall_seconds / exit / pytest 总结行，
>    可直接复核。（诚实备注：v1 时间戳经复测并非必然造假——telemetry 单轮实测
>    ~3.3s，与 v1 的 3s 轮间隔吻合——但 v1 证据本身无法自证，缺陷成立。）
> 3. **v1 压力覆盖缩窄**：spec §2.1 嫌疑面按字面含 glob（`test_eval_*`、`test_skill_*`）
>    与点名文件（`test_mcp`、`test_approval_semantics`），v1 只跑 7 个文件却声称
>    「全覆盖」。v2 修订：扩到全嫌疑面 **21 文件 × 5 轮 = 105 轮**（清单见下）。
> 4. **flaky-findings.json 键名与验收门不匹配**：v1 用 camelCase（`fullRuns`），
>    task_0001 的 verify 断言 `f['full_runs']`/`f['full_runs_log_dir']`，逐字执行
>    `KeyError: 'full_runs'`（exit 1），验收门从未以声明形式通过。v2 修订：JSON 键
>    改为 snake_case，与声明 verify 一致（文末附逐字运行结果）。

## 1. 检测协议（v2 修订版）

全部日志以重定向存全量（stdout+stderr，不管道过滤），每轮独立记录退出码
（`<log>.exit`），逐轮元数据（命令/起止/wall/exit/总结行）入机器可读 JSON：

- **全量层**：`uv run --extra dev python -m pytest`（**不带 CLI `-q`**，见修订 1）
  连续 4 轮 → `run-<n>.log`（n=1..4）；逐轮记录在 `full-runs.json`。串行执行，
  期间不并发任何其他 pytest 进程。
- **压力层**：全嫌疑面 21 文件（spec §2.1 字面清单：`test_telemetry_otlp`；
  `test_eval_*` 全部 9 个；`test_mcp`、`test_mcp_oauth`；`test_approval_semantics`；
  `test_skill_*` 全部 8 个）各连跑 5 轮（单文件、逐轮独立进程、串行、与全量层不
  并发，避免人为诱发 CPU/端口/tmp 竞争）→ `stress-<file>-<n>.log`（105 份）；
  逐轮记录在 `stress-runs.json`。

文件清单（21）：

```
tests/test_telemetry_otlp.py        tests/test_eval.py                  tests/test_eval_assertions_p1.py
tests/test_eval_baseline_ablation.py tests/test_eval_cost_p1.py         tests/test_eval_difficulty_p1.py
tests/test_eval_memory_p2.py        tests/test_eval_multiturn_p2.py     tests/test_eval_real_p3.py
tests/test_eval_suite.py            tests/test_mcp.py                   tests/test_mcp_oauth.py
tests/test_approval_semantics.py    tests/test_skill.py                 tests/test_skill_evolution.py
tests/test_skill_force_load.py      tests/test_skill_layers.py          tests/test_skill_propose.py
tests/test_skill_propose_pty.py     tests/test_skill_requirements.py    tests/test_skill_spec_compat.py
```

## 2. 轮次证据（v2 实测，2026-11-02 21:31–22:23）

### 全量层（4 轮，`full-runs.json` 同源）

| 轮次 | 起止（本地时间） | wall(s) | exit | pytest 总结行（日志末尾原文） |
|---|---|---|---|---|
| run-1 | 21:31:25 → 21:38:21 | 416.4 | 0 | `1614 passed, 2 skipped in 415.85s (0:06:55)` |
| run-2 | 21:38:25 → 21:45:21 | 416.1 | 0 | `1614 passed, 2 skipped in 415.72s (0:06:55)` |
| run-3 | 21:45:27 → 21:52:23 | 415.9 | 0 | `1614 passed, 2 skipped in 415.54s (0:06:55)` |
| run-4 | 21:52:26 → 21:59:22 | 415.8 | 0 | `1614 passed, 2 skipped in 415.46s (0:06:55)` |

每份日志含完整进度 + 总结行；进度全为 `.` 与 `s`，无 F/E/X 字符。

### 压力层（21 文件 × 5 轮 = 105 轮，`stress-runs.json` 同源）

- 105 轮 exit 码全 0；每轮均有 pytest 总结行（`N passed in Xs`）与独立起止时间戳。
- 墙钟区间 22:00:08 → 22:02:31（约 143s，串行逐轮真实执行）；最重文件
  `test_approval_semantics` 每轮 ~21.2-21.4s（19 passed），`test_telemetry_otlp`
  每轮 ~3s（10 passed），`test_eval_real_p3` 每轮 ~0.65s（54 passed），
  `test_skill_propose_pty` 每轮 ~0.55s（2 passed，全文件确实只有 2 个测试函数：
  `tests/test_skill_propose_pty.py:492` 与 `tests/test_skill_propose_pty.py:561`，
  无静默跳过）。逐轮数值见 `stress-runs.json`。

### 稳定性层（3 轮，`stability-runs.json` 同源）

| 轮次 | 起止（本地时间） | wall(s) | exit | pytest 总结行 |
|---|---|---|---|---|
| stability-run-1 | 22:02:41 → 22:09:36 | 415.5 | 0 | `1614 passed, 2 skipped in 415.14s (0:06:55)` |
| stability-run-2 | 22:09:39 → 22:16:34 | 415.4 | 0 | `1614 passed, 2 skipped in 415.01s (0:06:55)` |
| stability-run-3 | 22:16:38 → 22:23:33 | 414.9 | 0 | `1614 passed, 2 skipped in 414.52s (0:06:54)` |

### 汇总文件

`flaky-findings.json`（snake_case 键：`full_runs=4`、`findings=[]`、verdict=零命中）
已通过 task_0001 声明 verify 的逐字执行（见 §5）；4 全量 + 105 压力日志与 exit 文件
数目经装配脚本校验一致（`ASSEMBLE-OK full=4 stress=105 stability=3 all_exit_zero=True`）。

## 3. accepted_risk 结论

**零命中，按预授权分支接受风险，不改任何 src/tests 代码。**

- v2 共 4 轮全量 + 105 轮压力 = 109 次检测执行（另加 3 轮稳定性全量 + 1 轮门禁当场
  全量，合计 8 轮全量 + 105 轮压力），约 56 分钟全量 wall time + 143 秒压力 wall time，
  无一失败、无一 error、无一 xfail 意外通过；无 traceback 可录。
- 据此判定：在本机（darwin 27.2.0 arm64）当前 HEAD（spec/2026-11-02_fix-flaky-tests，
  基于 573abe0）上，无证据支持存在可复现 flaky 用例。
- 该判定的边界（诚实声明）：这是**有限轮次的经验性结论，不是统计证明**——
  8 轮全量 + 105 轮压力足以排除高频（如每 5 轮一遇及以上）的 flake，对低频
  （数十轮一遇、依赖特定时序/负载/并行度）的 flake 无排除能力；且本轮全量层为
  串行单进程执行，未覆盖 `-n` 并行分发下的资源竞争面。

## 4. 回填重启触发条件

满足任一条即视为本 risk 接受失效，**立即回填本 spec 分支重启检测协议**（4+5 轮全量
重跑 + 以失败文件为中心扩压）：

1. 任何一次全量 `uv run --extra dev python -m pytest -q`（本地或 CI）出现**同一条用例**
   在重复执行中有时通过有时失败（intermittent），无论是否能在本地复现；
2. CI 上出现本轮未捕获的 FAIL/ERROR，且重跑该文件单独通过（即「重跑即绿」信号）；
3. 同一文件 7 天内在 CI 被标记 retry/re-run 两次及以上；
4. 全量测试引入并行分发（`-n`/xdist）或测试基础设施变更（tmp 根、端口分配、
   事件循环策略）——协议前提变化，须重新检测而非沿用本结论。

重启时的最小动作：重跑本文件 §1 协议全量 4 轮 + 失败文件压力 5 轮，证据追加到本目录
（新文件用日期前缀，不覆盖本轮 `run-*.log` / `stress-*.log` / 逐轮元数据
`full-runs.json` / `stress-runs.json`），并更新 flaky-findings.json。

## 5. v2 采集后的门禁逐字执行记录

- task_0001 verify（`uv run python -c "...flaky-findings.json..."`，仓库根）：
  逐字运行，输出 `DETECTION-OK runs= 4 findings= 0`，exit 0（v1 同命令
  `KeyError: 'full_runs'`，exit 1）。
- task_0002 verify（零命中分支为 `verify: manual`，同时以声明命令
  `uv run --extra dev python -m pytest -q <7 文件>` 逐字复跑）：exit 0。
- task_0003 verify：`STABLE 3 consecutive green full runs` + 当场全量 exit 0。
