# 首帧体验优化：快照/流式/错误路径/非TTY - 任务拆解

## 使用规则
- 每个任务都要写清楚 `boundary` 和 `verify`；机器任务的 verify 以首个反引号 span 为执行的命令
- 如果一个任务没有验证方式，就不能开始；确无命令可执行的任务写 `verify: manual: <理由>`
- 发现的可执行问题必须回写本包，并在本轮做完
- 新任务包使用 `YYYY-MM-DD_<verb>-<object>`，详见 `references/naming-and-commits.md`
- verify 命令不得自引用本包 check 门禁（自指命令在「最近一条」语义下不可满足）

## 阶段一：实现
- [x] task_0001 快照开关：FeatureConfig.auto_snapshot（默认 false）+ agent.py/plan_execute.py/orchestrator.py 按开关跳过 pre/post-turn 自动快照
  - boundary: 只改 `src/nexuscli/config.py`（FeatureConfig 新增字段）、`src/nexuscli/agent/agent.py`（168-170 与 203 处按 `config.features.auto_snapshot` 跳过 create；`SnapshotService(self.cwd)` 构造也一并 gated）、`src/nexuscli/agent/plan_execute.py`（64-66、110 及 260 注释处同样 gated）、`src/nexuscli/agent/orchestrator.py`（339-341 处同样 gated）、`tests/test_snapshot.py` 或新增测试文件；不改 /snapshot /restore 手动命令与 restore 语义
  - verify: `uv run pytest tests/test_snapshot.py tests/test_config.py`
- [x] task_0002 增量快照：create() 以 manifest（相对路径+mtime+size）与同项目最新一份快照比对，未变文件跳过拷贝
  - boundary: 只改 `src/nexuscli/snapshot/service.py`（新增 manifest 构建/比对/写入；`_copy_tree` 支持跳过目标已存在且指纹一致的文件）、`tests/test_snapshot.py`；不改 index.jsonl 行格式、不改 restore/clean/list 语义
  - depends-on: task_0001
  - verify: `uv run pytest tests/test_snapshot.py`
- [x] task_0003 单发流式：`-p` 模式消费 `engine.ask` 事件流，text_delta 即时输出；--json 保持一次性输出契约
  - boundary: 只改 `src/nexuscli/entrypoints/cli.py` 的 `_run_prompt`（react 分支走事件流 echo；plan/team 分支保持原样或最小适配但不改变其行为）、新增 `tests/test_cli_single_prompt.py`（stub engine 断言 delta 边写边出）；不改 `--json` 字段与 REPL 渲染
  - depends-on: task_0001
  - verify: `uv run pytest tests/test_cli_single_prompt.py tests/test_repl.py`
- [x] task_0004 非TTY降级：REPL 入口检测 stdin 非 tty 时打印一行引导（建议 `-p`）并以退出码 1 退出，不再 traceback
  - boundary: 只改 `src/nexuscli/entrypoints/repl.py` 的 `start_repl`（或 cli.py 调用分支）与新增 `tests/test_repl_non_tty.py`；不改 TTY 正常路径、不改 slash 命令实现
  - depends-on: task_0001
  - verify: `uv run pytest tests/test_repl_non_tty.py`
- [x] task_0005 全量回归：全仓库 pytest 通过，量化首帧改善（对默认关快照配置跑一次 `-p` 记录端到端秒数写入 checklist）
  - boundary: 只运行测试与实测记录，不改任何 src；若暴露回归，问题回写本包在轮内修复后重跑
  - depends-on: task_0002, task_0003, task_0004
  - verify: `uv run pytest`

---

**当前进度**：由脚本计算，无需手动维护
